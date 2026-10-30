// Build with Framework64 csc /target:exe /platform:x64 /langversion:5 and references
// System.Windows.Forms, System.Drawing, System.Web.Extensions, WebView2.Core/WinForms.
// stdout is exclusively UTF-8 JSONL. No listener, virtual-host mapping or JS bridge.
using System;
using System.Collections;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

internal static class NativeHostProgram
{
    internal const string Origin = "https://icode.localhost";
    internal const int MaxMessageBytes = 32 * 1024 * 1024;
    internal const int MaxRequestBodyBytes = 64 * 1024;
    internal const int MaxConcurrent = 16;
    internal const int RequestTimeoutMilliseconds = 30000;
    internal static readonly UTF8Encoding Utf8 = new UTF8Encoding(false, true);

    internal static JavaScriptSerializer Serializer()
    {
        return new JavaScriptSerializer { MaxJsonLength = MaxMessageBytes, RecursionLimit = 64 };
    }

    internal static string Text(IDictionary<string, object> message, string key)
    {
        object value;
        if (!message.TryGetValue(key, out value) || !(value is string))
            throw new InvalidDataException("Missing string field: " + key);
        return (string)value;
    }

    // Bound the encoded frame before allocating a JSON string; ReadLine has no limit.
    internal static Dictionary<string, object> ReadMessage(Stream input)
    {
        using (MemoryStream bytes = new MemoryStream())
        {
            while (true)
            {
                int next = input.ReadByte();
                if (next < 0)
                {
                    if (bytes.Length != 0) throw new InvalidDataException("Truncated JSONL frame");
                    return null;
                }
                if (next == 10) break;
                if (bytes.Length >= MaxMessageBytes) throw new InvalidDataException("JSONL frame exceeds 32 MiB");
                bytes.WriteByte((byte)next);
            }
            string line = Utf8.GetString(bytes.ToArray()).TrimEnd('\r');
            object parsed = Serializer().DeserializeObject(line);
            Dictionary<string, object> message = parsed as Dictionary<string, object>;
            if (message == null) throw new InvalidDataException("JSONL frame must be an object");
            return message;
        }
    }

    internal static bool IsOrigin(string value)
    {
        Uri uri;
        return Uri.TryCreate(value, UriKind.Absolute, out uri) &&
            uri.Scheme == "https" && uri.IdnHost == "icode.localhost" &&
            uri.Port == 443 && uri.UserInfo.Length == 0;
    }

    [STAThread]
    private static int Main(string[] arguments)
    {
        using (ProtocolOutput output = new ProtocolOutput())
        using (Stream input = new BufferedStream(Console.OpenStandardInput()))
        {
            try
            {
                foreach (string key in new string[] { "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
                    "WEBVIEW2_PIPE_FOR_SCRIPT_DEBUGGER", "WEBVIEW2_WAIT_FOR_SCRIPT_DEBUGGER", "WEBVIEW2_USER_DATA_FOLDER" })
                {
                    if (!String.IsNullOrEmpty(Environment.GetEnvironmentVariable(key)))
                        throw new InvalidDataException("WebView2 environment override is disabled: " + key);
                }
                string testScript = null;
                foreach (string argument in arguments)
                {
                    if (!argument.StartsWith("--test-script=", StringComparison.Ordinal) || testScript != null)
                        throw new InvalidDataException("Unsupported host argument");
                    testScript = argument.Substring("--test-script=".Length);
                    if (!Path.IsPathRooted(testScript)) throw new InvalidDataException("Test script path must be absolute");
                }
                Dictionary<string, object> init = ReadMessage(input);
                if (init == null) throw new InvalidDataException("EOF before init");
                if (Text(init, "type") != "init" || Text(init, "origin") != Origin)
                    throw new InvalidDataException("Unsupported init origin");
                string path = Text(init, "initial_path");
                string profile = Text(init, "user_data_dir");
                if (!path.StartsWith("/", StringComparison.Ordinal) || path.StartsWith("//", StringComparison.Ordinal) ||
                    !IsOrigin(Origin + path) || !Path.IsPathRooted(profile) ||
                    String.IsNullOrWhiteSpace(Text(init, "instance_id")))
                    throw new InvalidDataException("Invalid init fields");
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                using (NativeHostWindow window = new NativeHostWindow(input, output, profile, path, testScript))
                {
                    Application.Run(window);
                    return window.Failed ? 1 : 0;
                }
            }
            catch (Exception error)
            {
                output.Error(error);
                return 1;
            }
        }
    }
}

// Pipe writes happen on a worker so a stalled parent never blocks the STA event loop.
internal sealed class ProtocolOutput : IDisposable
{
    private readonly BlockingCollection<string> lines = new BlockingCollection<string>(64);
    private readonly Task writer;
    internal ProtocolOutput()
    {
        writer = Task.Run(delegate
        {
            using (Stream output = Console.OpenStandardOutput())
            {
                foreach (string line in lines.GetConsumingEnumerable())
                {
                    byte[] bytes = NativeHostProgram.Utf8.GetBytes(line + "\n");
                    output.Write(bytes, 0, bytes.Length);
                    output.Flush();
                }
            }
        });
    }
    internal void Send(Dictionary<string, object> message)
    {
        string line = NativeHostProgram.Serializer().Serialize(message);
        if (NativeHostProgram.Utf8.GetByteCount(line) > NativeHostProgram.MaxMessageBytes)
            throw new InvalidDataException("Outgoing JSONL frame exceeds 32 MiB");
        if (writer.IsCompleted || !lines.TryAdd(line)) throw new IOException("Parent output pipe unavailable");
    }
    internal void Event(string name)
    {
        Send(new Dictionary<string, object> { { "type", "event" }, { "event", name } });
    }
    internal void Error(Exception error)
    {
        string message = error.GetType().Name + ": " + error.Message;
        if (message.Length > 512) message = message.Substring(0, 512);
        Console.Error.WriteLine(message);
        try { Send(new Dictionary<string, object> { { "type", "event" }, { "event", "error" }, { "message", message } }); }
        catch (Exception) { }
    }
    public void Dispose()
    {
        lines.CompleteAdding();
        try { writer.Wait(1000); } catch (AggregateException) { }
    }
}

internal sealed class NativeHostWindow : Form
{
    private sealed class PendingRequest
    {
        internal readonly TaskCompletionSource<Dictionary<string, object>> Completion =
            new TaskCompletionSource<Dictionary<string, object>>();
        internal CoreWebView2WebResourceRequestedEventArgs Args;
        internal CoreWebView2Deferral Deferral;
        internal bool Finished;
    }

    private readonly Stream input;
    private readonly ProtocolOutput output;
    private readonly string profile;
    private readonly string initialPath;
    private readonly string testScript;
    private readonly WebView2 web = new WebView2();
    private readonly Dictionary<string, PendingRequest> pending = new Dictionary<string, PendingRequest>();
    private readonly CancellationTokenSource shutdown = new CancellationTokenSource();
    private readonly ToolStripTextBox address = new ToolStripTextBox();
    private readonly ToolStripButton back = new ToolStripButton("Back");
    private readonly ToolStripButton forward = new ToolStripButton("Forward");
    private bool closing;
    private bool ready;
    private long nextRequest;
    internal bool Failed { get; private set; }

    internal NativeHostWindow(Stream input, ProtocolOutput output, string profile, string initialPath, string testScript)
    {
        this.input = input;
        this.output = output;
        this.profile = profile;
        this.initialPath = initialPath;
        this.testScript = testScript;
        Text = "ICODE";
        Width = 1240;
        Height = 860;
        MinimumSize = new System.Drawing.Size(640, 480);
        ToolStrip toolbar = new ToolStrip();
        ToolStripButton reload = new ToolStripButton("Reload");
        ToolStripButton copy = new ToolStripButton("Copy link");
        ToolStripButton home = new ToolStripButton("Home");
        address.AutoSize = false;
        address.Width = 680;
        address.Text = NativeHostProgram.Origin + initialPath;
        toolbar.Items.AddRange(new ToolStripItem[] { back, forward, reload, home, address, copy });
        web.Dock = DockStyle.Fill;
        Controls.Add(web);
        Controls.Add(toolbar);
        toolbar.Dock = DockStyle.Top;
        back.Click += delegate { if (web.CoreWebView2 != null && web.CanGoBack) web.GoBack(); };
        forward.Click += delegate { if (web.CoreWebView2 != null && web.CanGoForward) web.GoForward(); };
        reload.Click += delegate { if (web.CoreWebView2 != null) web.Reload(); };
        home.Click += delegate { Navigate(NativeHostProgram.Origin + initialPath); };
        copy.Click += delegate { try { Clipboard.SetText(address.Text); } catch (Exception error) { output.Error(error); } };
        address.KeyDown += delegate(object sender, KeyEventArgs args)
        {
            if (args.KeyCode != Keys.Enter) return;
            args.SuppressKeyPress = true;
            Navigate(address.Text);
        };
        Shown += async delegate { await InitializeAsync(); };
        FormClosing += delegate { StopPending(); };
        FormClosed += delegate { try { output.Event("closed"); } catch (Exception) { } };
    }

    protected override bool ProcessCmdKey(ref Message message, Keys keyData)
    {
        if (keyData == (Keys.Control | Keys.L))
        {
            address.Focus();
            address.SelectAll();
            return true;
        }
        return base.ProcessCmdKey(ref message, keyData);
    }

    private void Navigate(string uri)
    {
        if (web.CoreWebView2 != null && NativeHostProgram.IsOrigin(uri)) web.CoreWebView2.Navigate(uri);
        else address.Text = web.Source == null ? NativeHostProgram.Origin + initialPath : web.Source.AbsoluteUri;
    }

    private void Post(Action action)
    {
        if (closing || IsDisposed) return;
        try { BeginInvoke(action); } catch (InvalidOperationException) { }
    }

    private async Task InitializeAsync()
    {
        // The reader owns stdin after init. Every state mutation is marshalled to STA.
        Thread reader = new Thread(ReadParent) { IsBackground = true, Name = "ICODE parent pipe" };
        reader.Start();
        try
        {
            CoreWebView2EnvironmentOptions options = new CoreWebView2EnvironmentOptions(
                "--disable-background-networking --disable-component-update --no-proxy-server");
            CoreWebView2Environment environment = await CoreWebView2Environment.CreateAsync(null, profile, options);
            if (closing) return;
            await web.EnsureCoreWebView2Async(environment);
            if (closing) return;
            CoreWebView2 core = web.CoreWebView2;
            core.Settings.AreDevToolsEnabled = false;
            core.Settings.AreHostObjectsAllowed = false;
            core.Settings.IsWebMessageEnabled = false;
            core.Settings.IsStatusBarEnabled = false;
            core.Settings.AreDefaultContextMenusEnabled = false;
            core.Settings.IsPasswordAutosaveEnabled = false;
            core.Settings.IsGeneralAutofillEnabled = false;
            core.Settings.IsBuiltInErrorPageEnabled = false;
            core.AddWebResourceRequestedFilter("*", CoreWebView2WebResourceContext.All,
                CoreWebView2WebResourceRequestSourceKinds.All);
            core.WebResourceRequested += ResourceRequested;
            core.NavigationStarting += delegate(object sender, CoreWebView2NavigationStartingEventArgs args)
            {
                if (NativeHostProgram.IsOrigin(args.Uri)) return;
                args.Cancel = true;
                if (args.IsUserInitiated) OpenExternal(args.Uri);
            };
            core.FrameNavigationStarting += delegate(object sender, CoreWebView2NavigationStartingEventArgs args)
            {
                if (!NativeHostProgram.IsOrigin(args.Uri) && args.Uri != "about:blank") args.Cancel = true;
            };
            core.NewWindowRequested += delegate(object sender, CoreWebView2NewWindowRequestedEventArgs args)
            {
                args.Handled = true;
                if (!args.IsUserInitiated) return;
                if (NativeHostProgram.IsOrigin(args.Uri)) Navigate(args.Uri);
                else OpenExternal(args.Uri);
            };
            core.PermissionRequested += delegate(object sender, CoreWebView2PermissionRequestedEventArgs args)
            { args.State = CoreWebView2PermissionState.Deny; };
            core.DownloadStarting += delegate(object sender, CoreWebView2DownloadStartingEventArgs args)
            { args.Cancel = true; };
            core.ProcessFailed += delegate { Fail(new IOException("WebView2 process failed")); };
            core.WindowCloseRequested += delegate { Close(); };
            core.SourceChanged += delegate { if (NativeHostProgram.IsOrigin(core.Source)) address.Text = core.Source; };
            core.HistoryChanged += delegate { back.Enabled = core.CanGoBack; forward.Enabled = core.CanGoForward; };
            core.NavigationCompleted += async delegate(object sender, CoreWebView2NavigationCompletedEventArgs args)
            {
                if (closing || ready) return;
                if (!args.IsSuccess) { Fail(new IOException("Initial navigation failed: " + args.WebErrorStatus)); return; }
                ready = true;
                output.Send(new Dictionary<string, object> {
                    { "type", "event" }, { "event", "ready" },
                    { "pid", Process.GetCurrentProcess().Id }, { "window_id", Handle.ToInt64() } });
                if (testScript != null) await RunTestScriptAsync();
            };
            core.Navigate(NativeHostProgram.Origin + initialPath);
        }
        catch (Exception error) { if (!closing) Fail(error); }
    }

    private static void OpenExternal(string value)
    {
        Uri uri;
        if (!Uri.TryCreate(value, UriKind.Absolute, out uri) ||
            (uri.Scheme != "http" && uri.Scheme != "https" && uri.Scheme != "mailto")) return;
        try { Process.Start(new ProcessStartInfo(value) { UseShellExecute = true }); }
        catch (Exception error) { Console.Error.WriteLine("External link: " + error.GetType().Name); }
    }

    private void ReadParent()
    {
        try
        {
            while (!shutdown.IsCancellationRequested)
            {
                Dictionary<string, object> message = NativeHostProgram.ReadMessage(input);
                if (message == null) { Post(delegate { Close(); }); return; }
                string type = NativeHostProgram.Text(message, "type");
                if (type == "close") { Post(delegate { Close(); }); return; }
                if (type != "response") throw new InvalidDataException("Expected response or close");
                string id = NativeHostProgram.Text(message, "id");
                Post(delegate
                {
                    PendingRequest request;
                    if (pending.TryGetValue(id, out request)) request.Completion.TrySetResult(message);
                });
            }
        }
        catch (Exception error) { Post(delegate { Fail(error); }); }
    }

    private void Fail(Exception error)
    {
        Failed = true;
        output.Error(error);
        Close();
    }

    private void StopPending()
    {
        if (closing) return;
        closing = true;
        shutdown.Cancel();
        // Snapshot: completing a task may synchronously finish its event handler.
        List<PendingRequest> all = new List<PendingRequest>(pending.Values);
        foreach (PendingRequest request in all)
        {
            try { SetErrorResponse(request.Args, 503); } catch (Exception) { }
            FinishRequest(request);
            request.Completion.TrySetResult(null);
        }
        pending.Clear();
    }

    private static byte[] ReadBody(Stream content)
    {
        if (content == null) return new byte[0];
        using (MemoryStream body = new MemoryStream())
        {
            byte[] buffer = new byte[8192];
            int count;
            while ((count = content.Read(buffer, 0, buffer.Length)) != 0)
            {
                if (body.Length + count > NativeHostProgram.MaxRequestBodyBytes)
                    throw new InvalidDataException("Request body exceeds 64 KiB");
                body.Write(buffer, 0, count);
            }
            return body.ToArray();
        }
    }

    private void SetResponse(CoreWebView2WebResourceRequestedEventArgs args, int status, byte[] body, string headers)
    {
        // Do not dispose this stream here: WebView2 reads it after deferral.Complete.
        // The COM stream adapter retains it until WebView2 has finished reading.
        args.Response = web.CoreWebView2.Environment.CreateWebResourceResponse(
            new MemoryStream(body, false), status, status >= 400 ? "Request failed" : "OK", headers);
    }

    private void SetErrorResponse(CoreWebView2WebResourceRequestedEventArgs args, int status)
    {
        SetResponse(args, status, new byte[0], "Content-Type: text/plain; charset=utf-8\r\n" +
            "Cache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\n" +
            "Content-Security-Policy: default-src 'none'; frame-ancestors 'none'\r\n");
    }

    private static void FinishRequest(PendingRequest request)
    {
        if (request.Finished) return;
        request.Finished = true;
        try { request.Deferral.Complete(); } catch (Exception error) { Console.Error.WriteLine(error.GetType().Name); }
    }

    private async void ResourceRequested(object sender, CoreWebView2WebResourceRequestedEventArgs args)
    {
        PendingRequest request = new PendingRequest { Args = args, Deferral = args.GetDeferral() };
        string id = null;
        try
        {
            SetErrorResponse(args, 502);
            // Explicit responses are mandatory: an unhandled request would reach the network.
            if (!NativeHostProgram.IsOrigin(args.Request.Uri))
            {
                SetErrorResponse(args, 403);
                return;
            }
            if (closing || pending.Count >= NativeHostProgram.MaxConcurrent)
            {
                SetErrorResponse(args, closing ? 503 : 429);
                return;
            }
            id = (++nextRequest).ToString(System.Globalization.CultureInfo.InvariantCulture);
            pending.Add(id, request);
            Dictionary<string, string> headers = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (KeyValuePair<string, string> header in args.Request.Headers) headers[header.Key] = header.Value;
            byte[] requestBody;
            try { requestBody = ReadBody(args.Request.Content); }
            catch (InvalidDataException) { SetErrorResponse(args, 413); return; }
            output.Send(new Dictionary<string, object> {
                { "type", "request" }, { "id", id }, { "method", args.Request.Method },
                { "url", args.Request.Uri }, { "headers", headers },
                { "body_base64", Convert.ToBase64String(requestBody) } });
            Task timeout = Task.Delay(NativeHostProgram.RequestTimeoutMilliseconds, shutdown.Token);
            Task finished = await Task.WhenAny(request.Completion.Task, timeout);
            if (request.Finished) return;
            Dictionary<string, object> response = finished == request.Completion.Task ? await request.Completion.Task : null;
            if (response == null)
            {
                SetErrorResponse(args, closing ? 503 : 504);
                return;
            }
            object rawStatus;
            if (!response.TryGetValue("status", out rawStatus) || !(rawStatus is int) ||
                (int)rawStatus < 100 || (int)rawStatus > 599)
                throw new InvalidDataException("Invalid response status");
            object rawHeaders;
            if (!response.TryGetValue("headers", out rawHeaders) || !(rawHeaders is Dictionary<string, object>))
                throw new InvalidDataException("Invalid response headers");
            StringBuilder headerText = new StringBuilder();
            foreach (KeyValuePair<string, object> header in (Dictionary<string, object>)rawHeaders)
            {
                if (!ValidHeaderName(header.Key) || !(header.Value is string) ||
                    ((string)header.Value).IndexOfAny(new char[] { '\r', '\n', '\0' }) >= 0)
                    throw new InvalidDataException("Invalid response header");
                headerText.Append(header.Key).Append(": ").Append((string)header.Value).Append("\r\n");
            }
            byte[] body = Convert.FromBase64String(NativeHostProgram.Text(response, "body_base64"));
            SetResponse(args, (int)rawStatus, body, headerText.ToString());
        }
        catch (Exception error)
        {
            output.Error(error);
            try { SetErrorResponse(args, 502); }
            catch (Exception) { }
        }
        finally
        {
            if (id != null) pending.Remove(id);
            FinishRequest(request);
        }
    }

    private static bool ValidHeaderName(string name)
    {
        if (name.Length == 0) return false;
        foreach (char c in name)
            if (!(c >= 'a' && c <= 'z') && !(c >= 'A' && c <= 'Z') && !(c >= '0' && c <= '9') &&
                "!#$%&'*+-.^_`|~".IndexOf(c) < 0) return false;
        return true;
    }

    private async Task RunTestScriptAsync()
    {
        try
        {
            FileInfo info = new FileInfo(testScript);
            if (info.Length > NativeHostProgram.MaxMessageBytes) throw new InvalidDataException("Test script too large");
            object[] steps = NativeHostProgram.Serializer().DeserializeObject(File.ReadAllText(testScript, NativeHostProgram.Utf8)) as object[];
            if (steps == null) throw new InvalidDataException("Test script must be an array");
            foreach (object value in steps)
            {
                if (closing) return;
                Dictionary<string, object> step = value as Dictionary<string, object>;
                if (step == null) throw new InvalidDataException("Invalid test step");
                string action = NativeHostProgram.Text(step, "action");
                if (action == "close") { Close(); return; }
                if (action == "wait")
                {
                    int delay = Convert.ToInt32(step["milliseconds"]);
                    if (delay < 0 || delay > 30000) throw new InvalidDataException("Invalid test delay");
                    await Task.Delay(delay, shutdown.Token);
                }
                else if (action == "script")
                {
                    if (!NativeHostProgram.IsOrigin(web.CoreWebView2.Source))
                        throw new InvalidDataException("Test script source escaped fixed origin");
                    string parameters = NativeHostProgram.Serializer().Serialize(new Dictionary<string, object> {
                        { "expression", NativeHostProgram.Text(step, "script") },
                        { "awaitPromise", true }, { "returnByValue", true } });
                    Task<string> evaluation = web.CoreWebView2.CallDevToolsProtocolMethodAsync("Runtime.evaluate", parameters);
                    // Leave room to observe the production 30-second request timeout.
                    if (await Task.WhenAny(evaluation, Task.Delay(45000, shutdown.Token)) != evaluation)
                        throw new TimeoutException("Test evaluation timeout");
                    Dictionary<string, object> result = NativeHostProgram.Serializer().DeserializeObject(await evaluation) as Dictionary<string, object>;
                    if (result.ContainsKey("exceptionDetails")) throw new InvalidDataException("Test script exception: " + NativeHostProgram.Serializer().Serialize(result["exceptionDetails"]));
                    Dictionary<string, object> remote = (Dictionary<string, object>)result["result"];
                    object returned;
                    remote.TryGetValue("value", out returned);
                    object stepId;
                    step.TryGetValue("id", out stepId);
                    if (stepId != null && (!(stepId is string) || ((string)stepId).Length > 128))
                        throw new InvalidDataException("Test result id must be a string of at most 128 characters");
                    output.Send(new Dictionary<string, object> {
                        { "type", "event" }, { "event", "test_result" }, { "id", stepId },
                        { "result", NativeHostProgram.Serializer().Serialize(returned) } });
                }
                else if (action == "screenshot")
                {
                    string path = NativeHostProgram.Text(step, "path");
                    if (!Path.IsPathRooted(path)) throw new InvalidDataException("Screenshot path must be absolute");
                    using (FileStream image = new FileStream(path, FileMode.CreateNew, FileAccess.Write))
                        await web.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, image);
                    output.Event("test_screenshot");
                }
                else throw new InvalidDataException("Unknown test action");
            }
        }
        catch (OperationCanceledException) { }
        catch (Exception error) { if (!closing) Fail(error); }
    }
}

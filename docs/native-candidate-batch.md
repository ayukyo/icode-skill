# Native candidate object reads

Native source capture hashes the working files as they are. It does not normalize line endings or invoke Git clean/smudge filters. When a working blob differs from its index object, capture must check whether the indexed object is a supported LFS pointer before choosing the recorded Git OID.

The native path groups those referenced full OIDs within one capture. Bounded `git cat-file --batch-check` requests validate each object's type and size. Only blobs at most 4096 bytes, the existing pointer content limit, are read with bounded `--batch` requests. Repeated OIDs share the read; unrelated objects are never enumerated. There is no cache across captures.

Input OID lists and batch output both obey the Git byte budget. Content batches account for their headers, bodies and separators. A validated small object whose batch framing alone exceeds a very small batch budget uses the previous bare `cat-file blob` read with the existing 4096-byte content limit. Protocol failures never use that fallback. See the [official batch protocol](https://git-scm.com/docs/git-cat-file).

Each response must match the requested full OID, blob type, canonical size, order, exact content length and separator, with no trailing output. Content is checked against the Git blob hash. A valid LFS pointer changes the recorded `git_oid` only when its SHA-256 and size match the actual working bytes. Ordinary files, large indexed blobs and unmatched pointers retain the working blob OID. The snapshot schema and canonical identity calculation are unchanged.

Missing objects, invalid types, malformed or incomplete responses, overflow and read failures now reject native capture. Earlier native LFS probing could swallow an object-read error as a non-pointer result; that permissive failure behavior is deliberately tightened. No partial candidate is published as success.

The shared Git reader accepts optional bounded `bytes` input through a temporary file, which provides EOF without a duplex pipe deadlock. Calls without input retain their previous stdin behavior. Windows uses the existing bounded queue reader; POSIX uses the existing selector path. The five-second read window remains, with bounded child termination/reaping and reader cleanup, including initialization failures. Capture checks its total deadline before and after native reads and before returning a snapshot. An already-started read and its bounded cleanup may outlast the total deadline, but cannot publish a successful result afterward.

The typed provider API and its business-entry restrictions are unchanged. Native SHA-1 and SHA-256 object names remain supported. The reader requests `GIT_NO_LAZY_FETCH=1`; older Git versions may not honor it, so this change does not establish read-only guarantees for every historical partial-clone configuration. The supported local-object path does not need a fetch.

Existing per-file race checks and Git-state rechecks remain. Capture is not an atomic filesystem snapshot. In particular, the existing final mutable `HEAD` tree-query window is outside this performance repair; concurrent external Git changes must be excluded when relying on a stable candidate. This repair does not claim to remove every pre-existing race window.

Focused tests cover real native batch counts, LFS and staged content, small budgets, protocol faults, SHA-256, file/index/HEAD drift, final-deadline rejection, symlinks/gitlinks, failure propagation to the public control entry, and subprocess/input cleanup. Platform skips and unavailable tools must be reported separately from successful assertions. Performance measurements from a disposable fixture do not prove application or device behavior.

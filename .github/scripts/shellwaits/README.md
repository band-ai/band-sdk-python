# Windows shell wait diagnostics

The helper enables and checks the current process token's existing
`SeDebugPrivilege`, as required by the Windows wait-chain API. CI builds it before
the tests, then verifies it afterward against a PowerShell thread blocked on a
mutex owned by the helper through native `WaitForSingleObject`. Releasing the
mutex must let PowerShell exit normally. Validation runs after a test failure
too; it never warms PowerShell before the original approval scenario.

After an existing shell command times out, the test observer records its live
process, thread, and Windows wait-chain state before normal subprocess cleanup.
The original command, stdin, assertions, and timeout remain unchanged. The probe
has its own three-second deadline and three-second termination deadline.

Records contain module basenames, CPU duration, thread states, wait types,
statuses, process/thread IDs, and cycle detection. Lock names, command arguments,
memory, and application content are excluded. No process is cloned or suspended.

Wait-chain traversal covers supported synchronization waits. Single-node chains
cannot rule out a blocked operation; managed multiple-object waits, unsupported
waits and COM calls without registered callbacks remain inconclusive. Truncated
chains retain partial nodes and record the native error code.

The [Windows WCT example](https://learn.microsoft.com/en-us/windows/win32/debug/using-wct)
documents privilege setup. Microsoft
[documents the managed-wait limitation](https://learn.microsoft.com/en-us/archive/msdn-magazine/2007/july/bugslayer-wait-chain-traversal).

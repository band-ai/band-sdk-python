using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text.Json;
using Microsoft.Win32.SafeHandles;

static class ShellWaits
{
    const int MaxThreads = 64;
    const int MaxNodes = 16;
    const uint OutOfProcess = 1;
    const int MoreData = 234;
    const int TooManyThreads = 565;
    const uint TokenAdjustPrivileges = 0x20;
    const uint TokenQuery = 0x8;
    const uint PrivilegeEnabled = 0x2;

    public static int Main(string[] args)
    {
        try
        {
            EnableDebugPrivilege();
            if (args is ["--self-test"]) return SelfTest();
            Capture(int.Parse(args.Single()));
            return 0;
        }
        catch (Exception error)
        {
            Write(new { phase = "capture.error", type = error.GetType().Name });
            return 1;
        }
    }

    static void EnableDebugPrivilege()
    {
        using var process = Process.GetCurrentProcess();
        if (!OpenProcessToken(process.Handle, TokenAdjustPrivileges | TokenQuery, out var token))
            throw new System.ComponentModel.Win32Exception();
        using (token)
        {
            if (!LookupPrivilegeValue(null, "SeDebugPrivilege", out var luid))
                throw new System.ComponentModel.Win32Exception();
            var privileges = new TokenPrivileges { Count = 1, Luid = luid, Attributes = PrivilegeEnabled };
            if (!AdjustTokenPrivileges(token, false, ref privileges, 0, IntPtr.Zero, IntPtr.Zero))
                throw new System.ComponentModel.Win32Exception();
            var error = Marshal.GetLastWin32Error();
            if (error != 0) throw new System.ComponentModel.Win32Exception(error);
        }
    }

    static void Capture(int pid)
    {
        using var process = Process.GetProcessById(pid);
        Write(new { phase = "process.state", pid, cpu_ms = process.TotalProcessorTime.TotalMilliseconds,
            modules = process.Modules.Cast<ProcessModule>().Select(module => module.ModuleName).ToArray() });
        foreach (ProcessThread thread in process.Threads.Cast<ProcessThread>().Take(MaxThreads))
        {
            Write(new { phase = "thread.state", pid, thread = thread.Id, state = thread.ThreadState.ToString(),
                wait = thread.ThreadState == System.Diagnostics.ThreadState.Wait ? thread.WaitReason.ToString() : null });
            WaitChain(thread.Id);
        }
    }

    static WaitNode[] WaitChain(int thread)
    {
        var session = OpenThreadWaitChainSession(0, IntPtr.Zero);
        if (session == IntPtr.Zero) throw new System.ComponentModel.Win32Exception();
        try
        {
            var nodes = new WaitNode[MaxNodes];
            uint count = MaxNodes;
            if (!GetThreadWaitChain(session, UIntPtr.Zero, OutOfProcess, (uint)thread, ref count, nodes, out var cycle))
            {
                var error = Marshal.GetLastWin32Error();
                Write(new { phase = "thread.wait.error", thread, error });
                if (error != MoreData && error != TooManyThreads) return [];
            }
            var chain = nodes.Take((int)Math.Min(count, MaxNodes)).ToArray();
            // Lock object names can contain application data; emit only type and status.
            Write(new { phase = "thread.wait", thread, cycle, nodes = chain.Select(node => new {
                type = node.Type.ToString(), status = node.Status.ToString(),
                pid = node.Type == ObjectType.Thread ? (uint?)node.ProcessId : null,
                tid = node.Type == ObjectType.Thread ? (uint?)node.ThreadId : null,
            }).ToArray() });
            return chain;
        }
        finally { CloseThreadWaitChainSession(session); }
    }

    const string NativeWaitSource = """
        using System;
        using System.Runtime.InteropServices;
        public static class NativeWait {
            [DllImport("kernel32.dll")]
            public static extern uint WaitForSingleObject(IntPtr handle, uint timeout);
        }
        """;

    static int SelfTest()
    {
        var name = "Local\\shell-wait-" + Guid.NewGuid().ToString("N");
        using var mutex = new Mutex(true, name);
        using var child = Process.Start(new ProcessStartInfo("powershell") {
            ArgumentList = { "-NoProfile", "-Command",
                $"Add-Type -TypeDefinition '{NativeWaitSource}'; " +
                $"$m=[Threading.Mutex]::new($false,'{name}'); Write-Output 'ready'; " +
                "if ([NativeWait]::WaitForSingleObject($m.SafeWaitHandle.DangerousGetHandle(),[uint32]::MaxValue) -ne 0) { throw 'native wait failed' }" },
            UseShellExecute = false, RedirectStandardOutput = true,
        }) ?? throw new InvalidOperationException();
        try
        {
            if (child.StandardOutput.ReadLineAsync().WaitAsync(TimeSpan.FromSeconds(5)).GetAwaiter().GetResult() != "ready")
                throw new InvalidOperationException();
            var deadline = Stopwatch.StartNew();
            while (deadline.Elapsed < TimeSpan.FromSeconds(5))
            {
                if (child.Threads.Cast<ProcessThread>().Any(thread => IsOwnedMutexWait(WaitChain(thread.Id))))
                {
                    if (child.HasExited) throw new InvalidOperationException();
                    mutex.ReleaseMutex();
                    if (!child.WaitForExit(5000) || child.ExitCode != 0) throw new InvalidOperationException();
                    Write(new { phase = "self-test.passed" });
                    return 0;
                }
                Thread.Sleep(10);
            }
            throw new InvalidOperationException();
        }
        finally
        {
            if (!child.HasExited) child.Kill(entireProcessTree: true);
            child.WaitForExit();
        }
    }

    static bool IsOwnedMutexWait(WaitNode[] chain) =>
        chain.Any(node => node.Type == ObjectType.Mutex) &&
        chain.Any(node => node.Type == ObjectType.Thread && node.ProcessId == (uint)Environment.ProcessId);

    enum ObjectType { CriticalSection = 1, SendMessage, Mutex, Alpc, Com, ThreadWait, ProcessWait, Thread, ComActivation, Unknown, SocketIo, SmbIo }
    enum ObjectStatus { NoAccess = 1, Running, Blocked, PidOnly, PidOnlyRpcss, Owned, NotOwned, Abandoned, Unknown, Error }

    // wct.h: two enums followed by a 272-byte union, aligned to eight bytes.
    [StructLayout(LayoutKind.Explicit, Size = 280)]
    struct WaitNode
    {
        [FieldOffset(0)] public ObjectType Type;
        [FieldOffset(4)] public ObjectStatus Status;
        [FieldOffset(8)] public uint ProcessId;
        [FieldOffset(12)] public uint ThreadId;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct Luid { public uint Low; public int High; }
    [StructLayout(LayoutKind.Sequential)]
    struct TokenPrivileges { public uint Count; public Luid Luid; public uint Attributes; }

    [DllImport("advapi32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    static extern bool OpenProcessToken(IntPtr process, uint access, out SafeAccessTokenHandle token);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    static extern bool LookupPrivilegeValue(string? system, string name, out Luid luid);
    [DllImport("advapi32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    static extern bool AdjustTokenPrivileges(SafeAccessTokenHandle token, [MarshalAs(UnmanagedType.Bool)] bool disableAll,
        ref TokenPrivileges privileges, uint length, IntPtr previous, IntPtr required);

    [DllImport("advapi32.dll", SetLastError = true)]
    static extern IntPtr OpenThreadWaitChainSession(uint flags, IntPtr callback);
    [DllImport("advapi32.dll")]
    static extern void CloseThreadWaitChainSession(IntPtr session);
    [DllImport("advapi32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    static extern bool GetThreadWaitChain(IntPtr session, UIntPtr context, uint flags, uint thread,
        ref uint count, [Out] WaitNode[] nodes, [MarshalAs(UnmanagedType.Bool)] out bool cycle);

    static void Write(object value) => Console.WriteLine(JsonSerializer.Serialize(value));
}

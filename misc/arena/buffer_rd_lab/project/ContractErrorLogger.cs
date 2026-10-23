using Godot;
using System.Threading;

public partial class ContractErrorLogger : Logger
{
    int _errors;
    public int Errors => Volatile.Read(ref _errors);

    public override void _LogError(string function, string file, int line,
        string code, string rationale, bool editorNotify, int errorType,
        Godot.Collections.Array<ScriptBacktrace> backtraces)
    {
        if (errorType != (int)ErrorType.Warning) Interlocked.Increment(ref _errors);
    }
}

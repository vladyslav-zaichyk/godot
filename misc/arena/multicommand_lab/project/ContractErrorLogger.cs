using Godot;
using System.Collections.Generic;

public partial class ContractErrorLogger : Logger
{
    readonly object _gate = new();
    readonly List<object> _errors = new();
    public object[] Errors { get { lock (_gate) return _errors.ToArray(); } }
    public override void _LogError(string function, string file, int line, string code,
        string rationale, bool editorNotify, int errorType, Godot.Collections.Array<ScriptBacktrace> backtraces)
    {
        lock (_gate) _errors.Add(new { function, file, line, code, rationale, error_type = errorType });
    }
}

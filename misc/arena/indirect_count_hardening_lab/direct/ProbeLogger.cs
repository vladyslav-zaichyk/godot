using Godot;
using System;
using System.Collections.Generic;
using System.Linq;

public partial class ProbeLogger : Logger
{
    public record Diagnostic(string kind,string message,string function,string code,string rationale,int error_type,string operation);
    readonly object _gate = new();
    readonly List<Diagnostic> _diagnostics = new();
    string _operation = "";
    int _dropped;
    public Diagnostic[] Details { get { lock (_gate) return _diagnostics.ToArray(); } }
    public object[] Observed => Details.Select(item => (object)new { item.kind,item.message }).ToArray();
    public int Dropped { get { lock (_gate) return _dropped; } }
    public void BeginExpected(string operation) { lock (_gate) { if (_operation.Length != 0) throw new InvalidOperationException("Nested expected diagnostic."); _operation = operation; } }
    public void EndExpected() { lock (_gate) _operation = ""; }
    public override void _LogError(string function,string file,int line,string code,string rationale,bool editorNotify,int errorType,
        Godot.Collections.Array<ScriptBacktrace> backtraces)
    {
        lock (_gate) {
            if (_diagnostics.Count >= 64) { _dropped = Math.Min(int.MaxValue-1,_dropped)+1; return; }
            string kind = errorType == 0 ? "error" : errorType == 1 ? "warning" : errorType == 2 ? "script" : "shader";
            string message = string.IsNullOrEmpty(rationale) ? code : rationale;
            _diagnostics.Add(new Diagnostic(kind,message,function,code,rationale,errorType,_operation));
        }
    }
}

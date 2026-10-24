using Godot;
using System.Collections.Generic;
using System.Linq;

public partial class NativeLogger : Logger
{
    readonly object _gate = new();
    const int Capacity = 128;
    record Entry(string kind,string message,string function,string file,int line,int error_type);
    readonly List<Entry> _details = new();
    int _dropped;
    public int Dropped { get { lock (_gate) return _dropped; } }
    public object[] Observed { get { lock (_gate) return _details.Select(entry=>(object)new{entry.kind,entry.message}).ToArray(); } }
    public object[] Details { get { lock (_gate) return _details.Cast<object>().ToArray(); } }
    public override void _LogError(string function, string file, int line, string code,
        string rationale, bool editorNotify, int errorType, Godot.Collections.Array<ScriptBacktrace> backtraces)
    {
        // Godot ErrorHandlerType WARNING=1, ERROR=0; do not suppress unexpected diagnostics.
        lock (_gate) {
            if (_details.Count == Capacity) { if(_dropped<int.MaxValue)_dropped++; return; }
            _details.Add(new Entry(errorType==1?"warning":"error",string.IsNullOrEmpty(rationale)?code:rationale,
                function,file,line,errorType));
        }
    }
}

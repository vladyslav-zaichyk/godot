using Godot;
using System;
using System.Collections.Generic;

public partial class ProbeLogger : Logger
{
    public record Entry(string function, string message, int error_type);
    readonly object _gate = new();
    readonly List<Entry> _entries = new();
    int _dropped;
    public Entry[] Entries { get { lock (_gate) return _entries.ToArray(); } }
    public int Dropped { get { lock (_gate) return _dropped; } }
    public override void _LogError(string function,string file,int line,string code,string rationale,
        bool editorNotify,int errorType,Godot.Collections.Array<ScriptBacktrace> backtraces)
    {
        lock (_gate) {
            if (_entries.Count == 64) { _dropped=Math.Min(int.MaxValue-1,_dropped)+1; return; }
            _entries.Add(new Entry(function,string.IsNullOrEmpty(rationale)?code:rationale,errorType));
        }
    }
}

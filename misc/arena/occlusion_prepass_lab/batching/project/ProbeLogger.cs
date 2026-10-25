using Godot;
using System;
using System.Collections.Generic;
using System.Text.RegularExpressions;

public partial class ProbeLogger : Logger
{
    readonly object _gate = new(); readonly List<object> _entries = new(); int _dropped;
    public object[] Entries { get { lock(_gate)return _entries.ToArray(); } }
    public int Dropped { get { lock(_gate)return _dropped; } }
    void Add(object entry){lock(_gate){if(_entries.Count>=64){_dropped++;return;}_entries.Add(entry);}}
    public override void _LogError(string function,string file,int line,string code,string rationale,
        bool editorNotify,int errorType,Godot.Collections.Array<ScriptBacktrace> backtraces)
    {Add(new{function,file,line,code,rationale,error_type=errorType});}
    public override void _LogMessage(string message,bool error)
    {if(error || Regex.IsMatch(message,@"\bOp\w+\s+is not supported yet\b"))Add(new{stderr=error,message});}
}

using Godot;
using System;
using System.Collections.Generic;

public partial class MeshRdErrorLogger : Logger
{
    readonly object _gate = new();
    readonly List<object> _unexpected = new();
    readonly List<ExpectedError> _expected = new();
    string _test = "", _rationale = "";
    int _matches;
    public object[] Unexpected { get { lock (_gate) return _unexpected.ToArray(); } }
    public ExpectedError[] Expected { get { lock (_gate) return _expected.ToArray(); } }
    public sealed record ExpectedError(string test, string function, string file, int line,
        string code, string rationale, int error_type);
    public void Begin(string test, string rationale)
    {
        lock (_gate) {
            if (_test.Length != 0) throw new InvalidOperationException("Nested native error scope.");
            _test = test; _rationale = rationale; _matches = 0;
        }
    }
    public bool End(string test)
    {
        lock (_gate) {
            bool matched = test == _test && _matches == 1;
            _test = _rationale = ""; _matches = 0;
            return matched;
        }
    }
    public override void _LogError(string function, string file, int line, string code, string rationale,
        bool editorNotify, int errorType, Godot.Collections.Array<ScriptBacktrace> backtraces)
    {
        lock (_gate) {
            string normalized = file.Replace('\\', '/');
            bool location = (function == "uniform_set_create" || function == "RenderingDevice::uniform_set_create") &&
                (normalized == "servers/rendering/rendering_device.cpp" || normalized.EndsWith("/servers/rendering/rendering_device.cpp", StringComparison.Ordinal));
            if (_test.Length != 0 && _matches == 0 && location && line > 0 && code.Length > 0 &&
                errorType == (int)ErrorType.Error && rationale == _rationale) {
                _expected.Add(new ExpectedError(_test, function, file, line, code, rationale, errorType));
                _matches++;
            } else _unexpected.Add(new { function, file, line, code, rationale, error_type = errorType });
        }
    }
}

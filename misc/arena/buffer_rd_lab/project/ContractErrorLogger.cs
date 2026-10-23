using Godot;
using System.Collections.Generic;
using System.Threading;

public partial class ContractErrorLogger : Logger
{
    int _errors;
    int _totalErrors;
    readonly object _gate = new();
    readonly List<ExpectedError> _expectedCallbacks = new();
    string _activeCase = "", _activeRationale = "";
    int _activeMatches;
    public int Errors => Volatile.Read(ref _errors);
    public int TotalErrors => Volatile.Read(ref _totalErrors);
    public ExpectedError[] ExpectedCallbacks { get { lock (_gate) return _expectedCallbacks.ToArray(); } }

    public sealed record ExpectedError(string test, string function, string file, int line,
        string code, string rationale, int error_type);

    public void BeginExpected(string test, string rationale)
    {
        lock (_gate) {
            if (_activeCase.Length != 0) throw new System.InvalidOperationException("Nested expected native error.");
            _activeCase = test; _activeRationale = rationale; _activeMatches = 0;
        }
    }

    public bool EndExpected(string test)
    {
        lock (_gate) {
            bool matched = _activeCase == test && _activeMatches == 1;
            _activeCase = _activeRationale = ""; _activeMatches = 0;
            return matched;
        }
    }

    public override void _LogError(string function, string file, int line,
        string code, string rationale, bool editorNotify, int errorType,
        Godot.Collections.Array<ScriptBacktrace> backtraces)
    {
        if (errorType == (int)ErrorType.Warning) return;
        Interlocked.Increment(ref _totalErrors);
        lock (_gate) {
            string normalizedFile = file.Replace('\\', '/');
            bool exactLocation = (function == "set_shader_parameter" || function == "ShaderMaterial::set_shader_parameter") &&
                (normalizedFile == "scene/resources/material.cpp" || normalizedFile.EndsWith("/scene/resources/material.cpp", System.StringComparison.Ordinal));
            if (_activeCase.Length != 0 && _activeMatches == 0 && exactLocation && line > 0 &&
                errorType == (int)ErrorType.Error && rationale == _activeRationale) {
                _expectedCallbacks.Add(new ExpectedError(_activeCase, function, file, line, code, rationale, errorType));
                _activeMatches++;
                return;
            }
        }
        Interlocked.Increment(ref _errors);
    }
}

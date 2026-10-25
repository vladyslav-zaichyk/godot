using Godot;
using System;

// Trusted, read-only diagnostic. Native TAA owns motion demand; all five needs/resolved flags are false.
public partial class HistoryCapture : CompositorEffect
{
    public Action<RenderData>? Capture;
    public HistoryCapture() { EffectCallbackType = EffectCallbackTypeEnum.PostOpaque; Enabled = true; }
    public override void _RenderCallback(int stage, RenderData data)
    { if (stage == (int)EffectCallbackTypeEnum.PostOpaque) Capture?.Invoke(data); }
}

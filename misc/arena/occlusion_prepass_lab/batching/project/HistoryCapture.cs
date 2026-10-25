using Godot;
using System;

// Native TAA owns motion demand. All five needs/resolved flags stay false.
public partial class HistoryCapture : CompositorEffect
{
    public Action<RenderData>? Capture;
    public HistoryCapture() { EffectCallbackType = EffectCallbackTypeEnum.PostOpaque; Enabled = true; }
    public override void _RenderCallback(int stage, RenderData data)
    { if (stage == (int)EffectCallbackTypeEnum.PostOpaque) Capture?.Invoke(data); }
}

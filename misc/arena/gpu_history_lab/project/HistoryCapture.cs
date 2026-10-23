using Godot;
using System;

// RenderData is never retained. The owner copies its UBO and samples its velocity now.
public partial class HistoryCapture : CompositorEffect
{
    public Action<RenderData>? Capture;
    public HistoryCapture()
    {
        EffectCallbackType = EffectCallbackTypeEnum.PostOpaque;
        NeedsMotionVectors = true;
        Enabled = true;
    }
    public override void _RenderCallback(int effectCallbackType, RenderData renderData)
    {
        if (effectCallbackType == (int)EffectCallbackTypeEnum.PostOpaque) Capture?.Invoke(renderData);
    }
}

from __future__ import annotations

from obs_captions.config import OverlayConfig


def overlay_css_variables(cfg: OverlayConfig) -> str:
    justify_content = {
        "top": "flex-start",
        "middle": "center",
        "bottom": "flex-end",
    }[cfg.position]
    text_transform = "uppercase" if cfg.uppercase else "none"

    variables = {
        "font-family": cfg.font_family,
        "font-size": f"{cfg.font_size}px",
        "font-weight": str(cfg.font_weight),
        "color": cfg.color,
        "partial-color": cfg.partial_color,
        "background": cfg.background,
        "outline-width": f"{cfg.outline_width}px",
        "outline-color": cfg.outline_color,
        "shadow": cfg.shadow,
        "justify-content": justify_content,
        "align": cfg.align,
        "max-lines": str(cfg.max_lines),
        "line-height": str(cfg.line_height),
        "padding": f"{cfg.padding}px",
        "letter-spacing": f"{cfg.letter_spacing}px",
        "fade-ms": f"{cfg.fade_ms}ms",
        "text-transform": text_transform,
    }
    lines = [":root{"]
    lines.extend(f"  --cap-{name}: {value};" for name, value in variables.items())
    lines.append("}")
    return "\n".join(lines) + "\n"

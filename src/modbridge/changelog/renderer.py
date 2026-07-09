"""Render a ChangeSet into the markdown changelog shown in SakuraUpdater's client GUI."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from jinja2 import Environment, StrictUndefined

from modbridge.domain.models import ChangeSet

_DEFAULT_TEMPLATE = """\
# {{ title }}

_{{ date }}_

{% if changeset.minecraft_new and changeset.minecraft_old != changeset.minecraft_new -%}
**Minecraft:** {{ changeset.minecraft_old or "?" }} → {{ changeset.minecraft_new }}

{% endif -%}
{% if changeset.updated -%}
## Updated
{% for c in changeset.updated %}
- **{{ c.display_name }}**
{%- if c.old_version and c.new_version %}: {{ c.old_version }} → {{ c.new_version }}{% endif %}
{%- if c.changelog %}
  - {{ c.changelog }}
{%- endif %}
{%- endfor %}

{% endif -%}
{% if changeset.added -%}
## Added
{% for c in changeset.added %}
- **{{ c.display_name }}**{% if c.new_version %} ({{ c.new_version }}){% endif %}
{%- endfor %}

{% endif -%}
{% if changeset.removed -%}
## Removed
{% for c in changeset.removed %}
- **{{ c.display_name }}**
{%- endfor %}

{% endif -%}
{% for note in changeset.extra_notes -%}
> {{ note }}
{% endfor %}
"""


def render_changelog(
    changeset: ChangeSet,
    version: str,
    title_format: str,
    now: datetime,
    template_path: Path | None = None,
) -> str:
    source = (
        template_path.read_text(encoding="utf-8") if template_path else _DEFAULT_TEMPLATE
    )
    env = Environment(undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True)
    return env.from_string(source).render(
        changeset=changeset,
        version=version,
        title=title_format.format(version=version),
        date=now.strftime("%Y-%m-%d %H:%M"),
    )

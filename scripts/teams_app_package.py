"""Build the Microsoft Teams app package that installs the Open SWE bot.

Fills ``examples/teams/manifest.json`` with the bot's Entra client id, draws the
two icons Teams requires from the dashboard's logo mark, and zips them together.
Upload the result in Teams under Apps → Manage your apps → Upload an app.

    uv run python scripts/teams_app_package.py --client-id <TEAMS_CLIENT_ID> [--name "open-swe-you"]

``make teams-package`` runs this with the client id from ``.env``.
"""

import argparse
import io
import json
import sys
import zipfile
from pathlib import Path
from uuid import UUID

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "examples" / "teams" / "manifest.json"
LOGO = ROOT / "ui" / "public" / "logo-mark.png"
DEFAULT_OUTPUT = ROOT / "dist" / "open-swe-teams.zip"

COLOR_SIZE = 192
# Teams crops the color icon to a rounded tile; the mark stays inside its safe area.
COLOR_MARK_SIZE = 120
OUTLINE_SIZE = 32


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def color_icon(logo: Image.Image) -> bytes:
    canvas = Image.new("RGBA", (COLOR_SIZE, COLOR_SIZE), (0, 0, 0, 0))
    mark = logo.resize((COLOR_MARK_SIZE, COLOR_MARK_SIZE), Image.Resampling.LANCZOS)
    offset = (COLOR_SIZE - COLOR_MARK_SIZE) // 2
    canvas.alpha_composite(mark, (offset, offset))
    return _png(canvas)


def outline_icon(logo: Image.Image) -> bytes:
    """White on transparent, as Teams requires, traced from the mark's alpha channel."""
    alpha = logo.resize((OUTLINE_SIZE, OUTLINE_SIZE), Image.Resampling.LANCZOS).getchannel("A")
    outline = Image.new("RGBA", (OUTLINE_SIZE, OUTLINE_SIZE), (255, 255, 255, 0))
    outline.putalpha(alpha)
    return _png(outline)


def manifest(client_id: str, name: str) -> bytes:
    data = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    data["id"] = client_id
    data["bots"][0]["botId"] = client_id
    data["name"] = {"short": name, "full": name}
    return (json.dumps(data, indent=2) + "\n").encode()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--client-id", required=True, help="the bot's Entra app (client) id")
    parser.add_argument("--name", default="Open SWE", help="name Teams shows for the bot")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        client_id = str(UUID(args.client_id))
    except ValueError:
        sys.exit(f"--client-id must be the bot's app (client) id, a GUID; got {args.client_id!r}")
    if not args.name.strip() or len(args.name) > 30:
        sys.exit("--name must be 1 to 30 characters, the most Teams allows")

    logo = Image.open(LOGO).convert("RGBA")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("manifest.json", manifest(client_id, args.name.strip()))
        package.writestr("color.png", color_icon(logo))
        package.writestr("outline.png", outline_icon(logo))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()

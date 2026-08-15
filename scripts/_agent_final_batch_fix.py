from pathlib import Path

path = Path(__file__).resolve().parent / "host_updater.py"
text = path.read_text(encoding="utf-8")
text = text.replace('status = "\n".join', 'status = "\\n".join')
path.write_text(text, encoding="utf-8")

"""Every Chinese interface string must have an English translation with the same {placeholders}.

Run:  .venv\\Scripts\\python.exe tests\\test_i18n.py
"""
import ast
import re
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from labstudio.i18n import EN  # noqa: E402

CJK = re.compile(r'[一-鿿]')
ALLOWED = {'Language / 语言', '中文'}  # shown in both languages on purpose
missing, bad = [], []
for path in sorted((ROOT / 'labstudio').rglob('*.py')):
    if path.name == 'i18n.py':
        continue
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and CJK.search(node.value):
            text = node.value
            if text in ALLOWED:
                continue
            if text not in EN:
                missing.append(f'{path.relative_to(ROOT)}:{node.lineno}: {text!r}')
for zh, en in EN.items():
    fields = lambda s: sorted(f for _, f, _, _ in string.Formatter().parse(s) if f)
    if fields(zh) != fields(en) or CJK.search(en):
        bad.append(f'{zh!r} -> {en!r}')
for line in missing:
    print('MISSING', line)
for line in bad:
    print('MISMATCH', line)
print(f'{len(EN)} translations · {len(missing)} missing · {len(bad)} placeholder mismatches')
sys.exit(1 if missing or bad else 0)

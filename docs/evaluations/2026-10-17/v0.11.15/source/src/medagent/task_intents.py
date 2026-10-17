"""Conservative literal task intent matching declared by trusted content packs."""
import re


def task_intent(text, request_phrases, cancel_phrases):
    matches = []
    for kind, phrases in (("request", request_phrases), ("cancel", cancel_phrases)):
        for phrase in phrases:
            for match in re.finditer(re.escape(phrase), text):
                prefix = re.split(r"[，。！？；,;!?\n]", text[: match.start()])[-1][-8:]
                negators = (
                    ("不", "没", "别", "取消", "放弃", "不用")
                    if kind == "request"
                    else ("不是", "并非", "没有说", "并不")
                )
                if not any(word in prefix for word in negators):
                    matches.append((match.start(), len(phrase), kind, phrase))
    if not matches:
        return None, None
    _, _, kind, phrase = max(matches)
    return kind, phrase

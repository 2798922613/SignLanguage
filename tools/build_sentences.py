#!/usr/bin/env python3
"""
dataset/词汇组句表.md → sentences.js 生成脚本

为什么需要它：
    组句在运行时只有 script.js 的 comboMap 一处生效，而 comboMap 手写在 JS 里，
    改一次句子就要手改代码，还容易和文档不一致（2026-10-06 就出现过：文档已把
    "你吃了吗" 改成 `你,吃,了`，代码里还是 `你,吃,了,不`，导致该句永远匹配不到）。
    本脚本把 dataset/词汇组句表.md 第一节的表格当作**唯一权威来源**，解析后生成 sentences.js：
        SENTENCES      —— comboMap 直接用它（键 → 句子名）
        SENTENCE_INFO  —— 词序拆解 / 词编号 / 图片名，供页面展示"今天 + 天气 + 好"
        SENTENCE_META  —— 句数、最长句词数、队列上限

    生成前做完整校验，任一条不通过就报错退出且**不写文件**：
        1. 键唯一；键内每个词都是 dataset/词汇编号表.txt 的主词（006 吃 / 007 米饭 / 020 和；
           编号表已不再写斜杠别名，但 load_word_ids 仍容错取斜杠前）；
        2. 「词数」「词序拆解」「词编号」「图片名」四列与键自洽；
        3. 键长 ≤ 队列上限（script.js 的词队列最多 6 词，超长永远凑不满）；
        4. 无前缀/后缀冲突：checkWordCombo 用 `队列串.endsWith(键)`，
           A 是 B 的前缀或后缀都会让 A 抢先命中并清空队列，B 永远出不来；
        5. 键里不含疑问词（吗/呢/句末的"不"）—— 疑问语气靠表情，见 md 第二节规则 6。

用法：
    cd SignLanguage
    python3 tools/build_sentences.py            # 校验 + 生成 sentences.js
    python3 tools/build_sentences.py --check    # 只校验，不写文件
"""

import os
import re
import sys

DOC = os.path.join('dataset', '词汇组句表.md')
WORD_TABLE = os.path.join('dataset', '词汇编号表.txt')
OUT_FILE = 'sentences.js'
QUEUE_LIMIT = 6          # 与 script.js addWordToQueue 的上限保持一致
IMAGE_BASE = '组词效果'   # 与 script.js 的 IMAGE_BASE 保持一致
QUESTION_TOKENS = ('吗', '呢')

# | 序号 | 句子 | 词序拆解 | comboMap 键 | 词数 | 词编号 | 图片 |
ROW_RE = re.compile(
    r'^\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*`([^`]+)`\s*\|'
    r'\s*(\d+)\s*\|\s*([\d,]+)\s*\|\s*([^|]+?)\s*\|\s*$'
)
WORD_RE = re.compile(r'^(\d{3})\s+(\S+)')


def load_word_ids(path):
    """主词 → 3 位编号。编号表现在一行一个主词（无斜杠别名），split('/') 只是向后容错。"""
    ids = {}
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            match = WORD_RE.match(line.strip())
            if match:
                ids[match.group(2).split('/')[0]] = match.group(1)
    if not ids:
        sys.exit('读取词表失败：%s 里没有 "001 我" 这样的行' % path)
    return ids


def parse_rows(path):
    rows = []
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            match = ROW_RE.match(line.rstrip('\n'))
            if not match:
                continue
            index, sentence, split, key, count, ids, image = match.groups()
            rows.append({
                'index': int(index),
                'sentence': sentence.strip(),
                'split': split.strip(),
                'key': key.strip(),
                'count': int(count),
                'ids': ids.strip(),
                # 文档里写的是 "组词效果/你好.png"，运行时只需要文件名（IMAGE_BASE 已在 script.js）
                'image': image.strip().split('/')[-1],
                'image_path': image.strip(),
            })
    if not rows:
        sys.exit('解析失败：%s 里没找到第一节的句子表格行' % path)
    return rows


def validate(rows, word_ids):
    errors = []
    keys = [row['key'] for row in rows]

    if len(set(keys)) != len(keys):
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        errors.append('键重复：%s' % '、'.join(dupes))

    indexes = [row['index'] for row in rows]
    if indexes != list(range(1, len(rows) + 1)):
        errors.append('序号不连续（应为 1..%d）：%s' % (len(rows), indexes))

    for row in rows:
        tag = '#%d %s' % (row['index'], row['sentence'])
        tokens = row['key'].split(',')

        if len(tokens) != row['count']:
            errors.append('%s：词数列 %d ≠ 键的词数 %d' % (tag, row['count'], len(tokens)))
        if [t.strip() for t in row['split'].split('+')] != tokens:
            errors.append('%s：词序拆解「%s」≠ 键「%s」' % (tag, row['split'], row['key']))
        expect_ids = ','.join(word_ids.get(t, '???') for t in tokens)
        if expect_ids != row['ids']:
            errors.append('%s：词编号 %s ≠ 词表推出的 %s' % (tag, row['ids'], expect_ids))
        if row['image_path'] != '%s/%s.png' % (IMAGE_BASE, row['sentence']):
            errors.append('%s：图片列 %s ≠ %s/句子名.png' % (tag, row['image_path'], IMAGE_BASE))
        if len(tokens) > QUEUE_LIMIT:
            errors.append('%s：%d 词超过队列上限 %d' % (tag, len(tokens), QUEUE_LIMIT))
        for token in tokens:
            if token not in word_ids:
                errors.append('%s：词「%s」不在 %s 里' % (tag, token, WORD_TABLE))
            if token in QUESTION_TOKENS:
                errors.append('%s：键含疑问词「%s」（疑问语气靠表情，不进键）' % (tag, token))
        if tokens[-1] == '不':
            errors.append('%s：键以「不」结尾（旧疑问写法），疑问句请用表情表达' % tag)

    tokenized = [k.split(',') for k in keys]
    for i, a in enumerate(tokenized):
        for j, b in enumerate(tokenized):
            if i == j:
                continue
            if b[-len(a):] == a:
                errors.append('后缀冲突：`%s` 是 `%s` 的后缀，后者永远打不出来' % (keys[i], keys[j]))
            if b[:len(a)] == a:
                errors.append('前缀冲突：`%s` 是 `%s` 的前缀，会抢先命中' % (keys[i], keys[j]))

    # 去重：同一对会被上面两个方向各报一次
    seen = set()
    unique = []
    for message in errors:
        if message not in seen:
            seen.add(message)
            unique.append(message)
    return unique


def js_string(text):
    return '"%s"' % text.replace('\\', '\\\\').replace('"', '\\"')


def emit(rows):
    used = sorted({t for row in rows for t in row['key'].split(',')})
    unused = sorted(set(load_word_ids(WORD_TABLE)) - set(used))
    longest = max(len(row['key'].split(',')) for row in rows)

    lines = [
        '// Auto-generated from %s by tools/build_sentences.py - DO NOT EDIT' % DOC,
        '// 组句的唯一权威来源是 %s 第一节表格；改句子请改 md 后重跑：python3 tools/build_sentences.py' % DOC,
        '',
        '// comboMap 键 -> 句子名（script.js 直接用这个对象做 endsWith 匹配）',
        'const SENTENCES = {',
    ]
    for row in rows:
        lines.append('    %s: %s,' % (js_string(row['key']), js_string(row['sentence'])))
    lines += ['};', '', '// 键 -> 词序拆解等展示信息（页面按 md 的「词序拆解」列显示）', 'const SENTENCE_INFO = {']
    for row in rows:
        tokens = row['key'].split(',')
        words = ', '.join(js_string(t) for t in tokens)
        lines.append('    %s: { index: %d, text: %s, words: [%s], split: %s, ids: %s, image: %s },' % (
            js_string(row['key']), row['index'], js_string(row['sentence']), words,
            js_string(row['split']), js_string(row['ids']), js_string(row['image'])))
    lines += [
        '};',
        '',
        'const SENTENCE_META = {',
        '    source: %s,' % js_string(DOC),
        '    count: %d,' % len(rows),
        '    maxWords: %d,' % longest,
        '    queueLimit: %d,' % QUEUE_LIMIT,
        '    usedWords: %d,' % len(used),
        '    unusedWords: [%s]' % ', '.join(js_string(w) for w in unused),
        '};',
        '',
    ]
    return '\n'.join(lines)


def main():
    check_only = '--check' in sys.argv[1:]
    rows = parse_rows(DOC)
    errors = validate(rows, load_word_ids(WORD_TABLE))
    if errors:
        print('%s 校验未通过（%d 处）：' % (DOC, len(errors)))
        for message in errors:
            print('  - ' + message)
        sys.exit(1)

    print('校验通过：%d 句，最长 %d 词（队列上限 %d），键无前缀/后缀冲突' % (
        len(rows), max(len(r['key'].split(',')) for r in rows), QUEUE_LIMIT))
    if check_only:
        return

    content = emit(rows)
    with open(OUT_FILE, 'w', encoding='utf-8') as fh:
        fh.write(content)
    print('已生成 %s（%d 字节）' % (OUT_FILE, len(content.encode('utf-8'))))


if __name__ == '__main__':
    main()

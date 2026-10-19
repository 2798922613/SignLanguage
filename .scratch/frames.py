import cv2, numpy as np, os
V = "dataset/video"
spec = [
    ("011", "高兴_s01_011.webm", [1.25, 1.85, 3.00]),
    ("012", "爱_s01_012.webm",   [1.40, 1.95, 3.20]),
    ("013", "喜欢_s01_013.webm", [1.10, 2.20, 3.00]),
    ("014", "回_s01_014.webm",   [1.30, 2.00, 2.90]),
    ("015", "家_s01_015.webm",   [1.20, 1.60, 2.80]),
]
CW, CH = 420, 236
rows = []
for lab, fn, ts in spec:
    cap = cv2.VideoCapture(os.path.join(V, fn))
    import json as _j
    fps = _j.load(open(os.path.join(V, fn.replace(".webm", ".json"))))["avgFps"]
    want = {int(round(t * fps)): t for t in ts}
    got = {}
    idx = 0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        if idx in want:
            got[want[idx]] = fr
        idx += 1
        if len(got) == len(want):
            break
    cap.release()
    cells = []
    for t in ts:
        fr = got.get(t)
        if fr is None:
            fr = np.zeros((CH, CW, 3), np.uint8)
        fr = cv2.resize(fr, (CW, CH))
        cv2.putText(fr, "%.2fs" % t, (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 2)
        cells.append(fr)
    label = np.full((CH, 76, 3), 30, np.uint8)
    cv2.putText(label, lab, (6, CH // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    rows.append(np.hstack([label] + cells))
grid = np.vstack(rows)
cv2.imwrite(".scratch/grid.png", grid)
print("wrote .scratch/grid.png", grid.shape)

import cv2, numpy as np, os, json
V = "dataset/video"
spec = [("013", "喜欢_s01_013.webm", 2.90), ("012", "爱_s01_012.webm", 3.20),
        ("011", "高兴_s01_011.webm", 3.00), ("014", "回_s01_014.webm", 2.90),
        ("015", "家_s01_015.webm", 2.80)]


def read_all(fn):
    cap = cv2.VideoCapture(os.path.join(V, fn))
    frs = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        frs.append(f)
    cap.release()
    return frs


cells = []
for lab, fn, t in spec:
    dur = json.load(open(os.path.join(V, fn.replace(".webm", ".json"))))["durationMs"] / 1000.0
    frs = read_all(fn)
    idx = min(len(frs) - 1, int(round(t / dur * len(frs))))
    fr = frs[idx] if frs else np.zeros((720, 1280, 3), np.uint8)
    h, w = fr.shape[:2]
    crop = fr[int(h * 0.40):int(h * 1.0), int(w * 0.25):int(w * 0.78)]
    crop = cv2.resize(crop, (430, 480))
    cv2.putText(crop, "%s n=%d" % (lab, len(frs)), (8, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
    cells.append(crop)
grid = np.vstack([np.hstack(cells[:3]), np.hstack(cells[3:] + [np.zeros((480, 430, 3), np.uint8)])])
cv2.imwrite(".scratch/crop.png", grid)
print("wrote", grid.shape, [len(read_all(f)) for _, f, _ in spec])

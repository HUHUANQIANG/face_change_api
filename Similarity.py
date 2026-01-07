# Python 示例（用insightface）
import cv2
import numpy as np
import insightface
from insightface.app import FaceAnalysis

app = FaceAnalysis(providers=['CUDAExecutionProvider'])  # 或 CPU
app.prepare(ctx_id=0, det_size=(640,640))

img1 = cv2.imread("similarity_test/001.png")
img2 = cv2.imread("similarity_test/001-1.png")

faces1 = app.get(img1)
faces2 = app.get(img2)

feat1 = faces1[0].normed_embedding  # 512维向量
feat2 = faces2[0].normed_embedding

similarity = np.dot(feat1, feat2)  # 余弦相似度（已归一化）
print("相似度:", similarity)
FROM python:3.12-slim
WORKDIR /app
COPY edge/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY edge/*.py ./
COPY models/defectnet.onnx models/test_split.txt /app/models/
# The "camera": the held-out test split only (270 images), never the training data.
COPY data/line /app/frames
ENV MODEL_PATH=/app/models/defectnet.onnx FRAMES_DIR=/app/frames FRAMES_LIST=/app/models/test_split.txt
CMD ["python", "worker.py"]

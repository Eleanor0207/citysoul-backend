# 後端的 Cloud Run 映像檔。
#
# 跟 Dockerfile.postgres 是兩件不相干的事：那個是本機開發用的資料庫映像檔
# （pgvector + PostGIS），只給 docker-compose 用，永遠不會上雲。這一個是
# 應用程式本身，跑在 Cloud Run 上。
#
# 依賴裝的是 requirements.txt 而不是 uv sync：requirements.txt 是從
# pyproject.toml + uv.lock 匯出的（見 README「requirements.txt 是產生出來的」），
# 版本一樣被鎖住，但映像檔裡不必多裝一個 uv。改過相依之後記得重新匯出，
# 否則這裡裝到的會是舊版本。

FROM python:3.12-slim

# PYTHONUNBUFFERED：不設的話 print/logging 會卡在 stdout buffer 裡，
# Cloud Logging 上看到的日誌會延遲、甚至在容器被砍掉時整段消失。
# PYTHONDONTWRITEBYTECODE：容器檔案系統是唯讀用途，.pyc 只是垃圾。
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# 先只複製 requirements.txt 再裝套件，最後才複製程式碼。改一行程式碼不該
# 讓 pip install 那層失效——這一層是整個建置最慢的部分。
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini ./
COPY app ./app
COPY migrations ./migrations
COPY scripts ./scripts
# 資料檔要跟著映像檔走，因為讀它們的是 Cloud Run Job：
#   data/     區界 GeoJSON（scripts/load_districts.py）
#   content/  地標史實與行政區指派 YAML（scripts/import_landmarks.py）
#
# ⚠️ 新增這類目錄時記得回來加一行。少了它，Job 會在雲端才報找不到檔案，而本機
# 跑同一支腳本是好的——這個差異已經發生過兩次。
COPY data ./data
COPY content ./content

# ⚠️ 不要在這裡寫 EXPOSE 8080 就當成埠號設定好了。Cloud Run 是用 $PORT
# 環境變數告訴容器要聽哪個埠，而且它可以不是 8080。硬寫 --port 8080 在多數
# 情況下能跑，但那是巧合而不是契約。
#
# exec 形式（JSON 陣列）不會有 shell 做變數展開，所以這裡用 shell 形式讓
# $PORT 能被展開；`exec` 讓 uvicorn 取代 shell 成為 PID 1，否則 SIGTERM
# 會送給 shell，uvicorn 收不到關機訊號，Cloud Run 縮容時連線會被硬斷。
#
# 不設 --workers：Cloud Run 的擴縮單位是容器實例，不是行程。多開 worker 只會
# 讓每個實例吃更多記憶體，並且讓併發數的計算變成兩層。
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}

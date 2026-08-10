FROM pgvector/pgvector:pg16

# 安裝 PostGIS 3 extension，實現 pgvector 與 postgis 雙擴充功能並存
RUN apt-get update && \
    apt-get install -y postgresql-16-postgis-3 && \
    rm -rf /var/lib/apt/lists/*

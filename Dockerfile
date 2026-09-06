FROM python:3.11-slim

LABEL org.opencontainers.image.title="GEWS" \
      org.opencontainers.image.description="Global Early Warning System — InSAR pipeline for satellite detection of unstable glaciers and rock slopes"

# GDAL/rasterio and h5py need these system libraries; build-essential is
# required to build any deps without prebuilt wheels for the target arch.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libgdal-dev \
        libhdf5-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency metadata first so dependency installation is cached
# independently of source changes.
COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir -e .

COPY config ./config

# Runtime data/output directories (mounted as volumes in practice)
RUN mkdir -p /app/data /app/output

ENTRYPOINT ["gews"]
CMD ["--help"]

FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential cmake ninja-build libgtest-dev nlohmann-json3-dev \
    && rm -rf /var/lib/apt/lists/*

ENV LC_ALL=C.UTF-8
WORKDIR /src

CMD ["sh", "-ec", "cmake -S /src -B /build -G Ninja -DCMAKE_BUILD_TYPE=Release && cmake --build /build --parallel 2 && ctest --test-dir /build --output-on-failure"]

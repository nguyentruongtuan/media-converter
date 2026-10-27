REGISTRY ?= registry.example.com
VERSION ?= 0.1.0
SAMPLE ?= /tmp/sample.mp4

.PHONY: install test test-docker lint up down logs sample e2e chaos images push deploy deploy-monitoring

install:
	python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'

test:
	.venv/bin/pytest -q

test-docker:  ## full suite including real FFmpeg transcodes
	docker build --target test -t media-converter:test .
	docker run --rm media-converter:test pytest -q -p no:cacheprovider

lint:
	.venv/bin/ruff check app tests alembic scripts
	.venv/bin/ruff format --check app tests alembic scripts
	python3 scripts/render_prometheusrule.py --check

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f api worker scheduler

sample:  ## 10s 1080p test pattern with audio
	docker compose exec -T worker ffmpeg -v error -y -f lavfi -i testsrc2=size=1920x1080:rate=30:duration=10 \
		-f lavfi -i sine=frequency=440:duration=10 -c:v libx264 -pix_fmt yuv420p -c:a aac /scratch/sample.mp4
	docker compose cp worker:/scratch/sample.mp4 $(SAMPLE)

e2e:
	python3 scripts/e2e.py $(SAMPLE)

chaos:
	docker compose up -d --scale worker=3
	python3 scripts/chaos_test.py $(SAMPLE) --jobs 4 --kill-cmd 'docker kill -s KILL {host}'

images:
	docker build -t $(REGISTRY)/media-converter:$(VERSION) .
	docker build -f deploy/nginx/Dockerfile -t $(REGISTRY)/media-converter-nginx:$(VERSION) .
	docker build -t $(REGISTRY)/media-converter-minio:RELEASE.2025-04-22T22-12-26Z deploy/minio

push: images
	docker push $(REGISTRY)/media-converter:$(VERSION)
	docker push $(REGISTRY)/media-converter-nginx:$(VERSION)
	docker push $(REGISTRY)/media-converter-minio:RELEASE.2025-04-22T22-12-26Z

deploy:
	kubectl apply -k deploy/k8s

deploy-monitoring:
	kubectl apply -k deploy/monitoring

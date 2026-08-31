IMAGE ?= v8help:0.10.0
CONTAINER ?= v8help
PORT ?= 8000
DATA_DIR ?= ./data
EMBEDDER_URL ?= http://host.docker.internal:11434/v1

.PHONY: build run test stop logs

build:
	docker build -t $(IMAGE) .

# --add-host нужен на Linux-движке, чтобы host.docker.internal указывал на хост.
# Перед первым запуском: mkdir -p ./data && sudo chown 1000:1000 ./data
run:
	docker run -d --name $(CONTAINER) \
		-p $(PORT):8000 \
		-v $(DATA_DIR):/data \
		--add-host host.docker.internal:host-gateway \
		-e V8HELP_EMBEDDER_QUERY_BASE_URL=$(EMBEDDER_URL) \
		$(IMAGE)

test:
	docker run --rm --entrypoint python $(IMAGE) -m pytest /app/tests -q

stop:
	docker rm -f $(CONTAINER)

logs:
	docker logs -f $(CONTAINER)

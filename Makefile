.PHONY: harness harness-clean

# Build the omp extension + reply-cli into harness/dist/.
# Consumed by the paseo Arch image (COPY harness/dist -> /opt/alicedev) and by
# the local dev daemon (omp -e harness/dist/omp-extension). Needs npm registry
# access; esbuild externalizes @oh-my-pi/* so the bundle builds without omp.
harness:
	cd harness && npm ci && npm run build

harness-clean:
	rm -rf harness/dist harness/node_modules

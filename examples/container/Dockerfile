# syntax=docker/dockerfile:1
FROM quay.io/condaforge/miniforge3:26.7.2-0 AS bootstrap

FROM ubuntu:24.04 AS build
COPY --from=bootstrap /opt/conda /opt/conda
RUN /opt/conda/bin/conda install --yes --override-channels -c conda-forge \
    conda-workspaces=0.9.0 conda-pypi
WORKDIR /opt/workspace
COPY conda.toml conda.lock pyproject.toml workspace_container_example.py activate.sh ./
COPY build_environment.py /tmp/build_environment.py
RUN /opt/conda/bin/python /tmp/build_environment.py

FROM ubuntu:24.04
WORKDIR /opt/workspace
COPY --from=build /opt/workspace/.conda/envs/runtime /opt/workspace/.conda/envs/runtime
COPY conda.toml conda.lock pyproject.toml workspace_container_example.py activate.sh ./
COPY --from=build /entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["workspace-hello"]

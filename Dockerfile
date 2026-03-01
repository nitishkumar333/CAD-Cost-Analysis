# Use Miniconda3 as the base image
FROM continuumio/miniconda3:latest

# Set metadata
LABEL maintainer="User"
LABEL description="Persistent pythonocc-core environment"

# Install system libraries required by OpenCASCADE (OCCT)
# FIX: Replaced 'libgl1-mesa-glx' with 'libgl1' for newer Debian versions
# Added 'libxext6' which is often needed for backend display checks
RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    libxrender1 \
    libxext6 \
    && rm -rf /var/lib/apt/lists/*

# Set the working directory
WORKDIR /workspace

# Create the conda environment
RUN conda create -n occ_env -c conda-forge \
    pythonocc-core \
    python=3.13 \
    jupyterlab \
    -y && \
    conda clean -afy

# Set the shell to ensure subsequent RUN commands use the conda env
SHELL ["conda", "run", "-n", "occ_env", "/bin/bash", "-c"]

# Expose port for Jupyter Lab
EXPOSE 8888

# Set up the entrypoint
ENTRYPOINT ["conda", "run", "--no-capture-output", "-n", "occ_env"]

# Default command
CMD ["/bin/bash"]
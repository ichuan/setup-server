# Setup Server

Use [fabric](https://www.fabfile.org/) to quickly setup a debian server

## Install
Python3+ and poetry are required

```
poetry install --without dev
```

## Usage

```
# list available tasks
poetry run fab -l

# setup debian
poetry run fab -H host1 debian

# enable a 2 GB swapfile
poetry run fab -H root@1.2.4.8 swap -g 2

# install nodejs, python3, and docker
poetry run fab -H host2 nodejs python docker
```

import json
import os
import textwrap
import urllib.request
from functools import total_ordering
from itertools import zip_longest
from typing import Self

from fabric import Connection, task
from patchwork.files import append, contains, exists


@total_ordering
class Version:
    def __init__(self, ver: str):
        self.value = ver
        self.ver1, self.ver2 = self.normalize(ver)

    def normalize(self, ver: str) -> tuple[list[int], list[int | str]]:
        y = ver.split('-', 1)
        if len(y) == 2:
            y1, y2 = y
        else:
            y1, y2 = y[0], ''
        return [int(i or 0) for i in y1.split('.')], [
            int(i) if i.isdigit() else i for i in y2.split('-')
        ]

    def __eq__(self, other: Self) -> bool:
        return self.value == other.value

    def __lt__(self, other: Self) -> bool:
        for i, j in zip_longest(self.ver1, other.ver1, fillvalue=0):
            if i != j:
                return i < j
        for i, j in zip_longest(self.ver2, other.ver2, fillvalue=0):
            if type(i) is not type(j):
                i, j = str(i), str(j)
            if i != j:
                return i < j
        return False


@task
def debian(c: type[Connection]):
    """
    Setup a debian server
    """
    # sudo
    if not c.run('which sudo', warn=True).ok:
        c.run('apt-get install sudo -y')
    # apt-get
    c.sudo('apt-get update -yq')
    c.sudo(
        'DEBIAN_FRONTEND=noninteractive '
        'apt-get -yq -o Dpkg::Options::="--force-confdef" '
        '-o Dpkg::Options::="--force-confold" upgrade'
    )
    c.sudo(
        'apt-get install -yq git unzip curl wget tar sudo zip '
        'sqlite3 tmux ntp build-essential gettext libcap2-bin netcat-traditional '
        'silversearcher-ag htop jq dirmngr cron rsync locales net-tools'
    )
    # add-apt-repository
    c.sudo('apt-get install -yq software-properties-common', warn=True)
    # c.sudo('systemctl enable ntp.service')
    # c.sudo('systemctl start ntp.service')
    # dotfiles
    dotfiles(c)
    # UTC timezone
    c.sudo('cp /usr/share/zoneinfo/UTC /etc/localtime', warn=True)
    # limits.conf, max open files
    c.run(
        r'echo -e "*    soft    nofile  500000\n*    hard    nofile  500000'
        r'\nroot soft    nofile  500000\nroot hard    nofile  500000"'
        r' | sudo tee /etc/security/limits.conf'
    )
    # https://underyx.me/2015/05/18/raising-the-maximum-number-of-file-descriptors
    line = 'session required pam_limits.so'
    for p in ('/etc/pam.d/common-session', '/etc/pam.d/common-session-noninteractive'):
        if exists(c, p) and not contains(c, p, line):
            append(c, p, line)
    # "systemd garbage"
    systemd_conf = '/etc/systemd/system.conf'
    if exists(c, systemd_conf):
        c.sudo(
            'sed -i '
            f'"s/^#DefaultLimitNOFILE=.*/DefaultLimitNOFILE=500000/g" {systemd_conf}',
            warn=True,
        )
    # sysctl.conf
    path = '/etc/sysctl.conf'
    for line in (
        'vm.overcommit_memory = 1',
        'net.core.somaxconn = 65535',
        'fs.file-max = 6553560',
    ):
        if not contains(c, path, line):
            append(c, path, line)
    c.sudo('sysctl -p')
    # disable ubuntu upgrade check
    c.sudo(
        "sed -i 's/^Prompt.*/Prompt=never/' /etc/update-manager/release-upgrades",
        warn=True,
    )
    # locale
    c.run('echo en_US.UTF-8 UTF-8 | sudo tee /etc/locale.gen')
    c.sudo('locale-gen en_US.UTF-8')
    # bbr
    bbr(c)
    # disable ipv6
    for line in [
        'net.ipv6.conf.all.disable_ipv6 = 1',
        'net.ipv6.conf.default.disable_ipv6 = 1',
        'net.ipv6.conf.lo.disable_ipv6 = 1',
    ]:
        if not contains(c, '/etc/sysctl.conf', line):
            append(c, '/etc/sysctl.conf', line)
    c.sudo('sysctl -p')


@task
def dotfiles(c: type[Connection]):
    """
    dotfiles
    """
    c.run(
        '[ ! -f ~/.tmux.conf ] && { '
        'wget https://github.com/ichuan/dotfiles/releases/latest/download/dotfiles.'
        'tar.gz -O - | tar xzf - && bash dotfiles/bootstrap.sh -f; }',
        warn=True,
    )
    c.run('rm -rf dotfiles ~/Tomorrow_Night_Bright.terminal ~/iTerm.profile.json')


@task
def bbr(c: type[Connection]):
    """
    Install Google BBR: https://github.com/google/bbr
    """
    if c.sudo(
        'sysctl net.ipv4.tcp_available_congestion_control | grep -q bbr', warn=True
    ).ok:
        print('bbr already enabled')
        return
    kernel_version = c.run('uname -r', hide=True).stdout.strip()
    if Version(kernel_version) < Version('4.9'):
        print('bbr need linux 4.9+, please upgrade your kernel')
        return
    for line in [
        'net.core.default_qdisc = fq',
        'net.ipv4.tcp_congestion_control = bbr',
    ]:
        if not contains(c, '/etc/sysctl.conf', line):
            append(c, '/etc/sysctl.conf', line)
    c.sudo('sysctl -p')


@task
def nodejs(c: type[Connection]):
    """
    Install latest Node.js
    """
    versions = json.load(
        urllib.request.urlopen(
            # https://nodejs.org/dist/index.json
            'https://registry.npmmirror.com/-/binary/node/index.json'
        )
    )
    lts = sorted(
        (i for i in versions if i['lts']), key=lambda i: Version(i['version'])
    )[-1]
    # already has?
    if c.run(f'which node && test `node --version` = "{lts["version"]}"', warn=True).ok:
        print('Already installed nodejs')
        return
    dist_url = f'https://nodejs.org/dist/latest-{lts['lts'].lower()}/node-{lts['version']}-linux-x64.tar.xz'
    c.run(f'wget -O /tmp/node.tar.xz --tries 3 {dist_url}')
    c.sudo(
        'tar -C /usr/ --exclude CHANGELOG.md --exclude LICENSE '
        '--exclude README.md --strip-components 1 -xf /tmp/node.tar.xz'
    )
    # can listening on 80 and 443
    # c.sudo('setcap cap_net_bind_service=+ep /usr/bin/node')


def _get_output(c: type[Connection], cmd: str) -> str:
    result = c.run(cmd, hide=True)
    return result.stdout.strip()


@task
def docker(c: type[Connection]):
    """
    Install docker and docker-compose on debian/ubuntu
    """
    # https://docs.docker.com/engine/install/debian/
    if c.run('which docker', warn=True).ok:
        print('Already installed docker')
        return
    c.sudo('apt update -yq')
    c.sudo('apt install -yq apt-transport-https ca-certificates curl')
    c.sudo('install -m 0755 -d /etc/apt/keyrings')
    c.sudo(
        'curl -fsSL https://download.docker.com/linux/debian/gpg '
        '-o /etc/apt/keyrings/docker.asc'
    )
    c.sudo('chmod a+r /etc/apt/keyrings/docker.asc')
    codename = _get_output(c, 'lsb_release -sc')
    c.run(
        'echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] '
        f'https://download.docker.com/linux/debian {codename} stable" '
        '| sudo tee /etc/apt/sources.list.d/docker.list'
    )
    c.sudo('apt update -yq')
    c.sudo(
        'apt install -yq docker-ce docker-ce-cli containerd.io docker-compose-plugin'
    )
    # docker logging rotate
    c.run(
        r"""echo -e '{\n  "log-driver": "json-file",\n  "log-opts": """
        r"""{\n    "max-size": "100m",\n    "max-file": "5"\n  }\n}' """
        r"""| sudo tee /etc/docker/daemon.json"""
    )
    c.sudo('service docker restart', warn=True)
    # fix permission issue
    if c.run('test $USER = root', warn=True).failed:
        c.run('sudo usermod -a -G docker $USER', warn=True)


@task(help={'gb': 'Size of the swapfile (GB)'})
def swap(c: type[Connection], gb: int = 1):
    """
    Install a swapfile, default to 1GB
    """
    path = f'/swap{gb}G'
    if c.run(f'test -f {path}', warn=True).ok:
        print(f'{path} already exists')
        return
    c.sudo(f'fallocate -l {gb}G {path}')
    c.sudo(f'chmod 600 {path}')
    c.sudo(f'mkswap {path}')
    c.sudo(f'swapon {path}')
    if not contains(c, '/etc/sysctl.conf', 'vm.swappiness=10'):
        append(c, '/etc/sysctl.conf', 'vm.swappiness=10')
    line = f'{path} none swap sw 00'
    if not contains(c, '/etc/fstab', line):
        append(c, '/etc/fstab', line)


@task(help={'version': 'which latest version to install. default: 3'})
def python(c: type[Connection], version: str = '3'):
    """
    Install pyenv, latest python3 and poetry
    """
    # Prerequisites: git, dotfiles (in debian)
    if not exists(c, '~/.pyenv'):
        c.run('curl https://pyenv.run | bash')
        c.sudo('apt update -yq')
        c.sudo(
            'apt install -y build-essential checkinstall libncursesw5-dev libssl-dev '
            'libsqlite3-dev tk-dev libgdbm-dev libc6-dev libbz2-dev libffi-dev '
            'libreadline-dev liblzma-dev zlib1g-dev'
        )
        if c.run('test -f ~/.bash_profile && grep -q pyenv ~/.bash_profile').ok:
            pass
        else:
            c.run(
                r'echo -e "export PYENV_ROOT=\"\$HOME/.pyenv\"\n'
                r'export PATH=\"\$PYENV_ROOT/bin:\$PATH\"\n'
                r'command -v pyenv > /dev/null && eval \"\$(pyenv init --path)\"" '
                r'>> ~/.bash_profile'
            )
    c.run(f'source ~/.bash_profile && pyenv install {version}:latest', warn=True)
    _poetry(c)


def _poetry(c: type[Connection]):
    """
    Install poetry based on pyenv
    """
    _sh = textwrap.dedent(
        r"""
        export PATH="$HOME/.pyenv/bin:$PATH"
        export PYENV_VERSION=`pyenv versions --bare --skip-aliases|sort -V | tail -n 1`
        curl -sSL https://install.python-poetry.org | pyenv exec python -
        # bin
        echo "export PATH=\"\$HOME/.local/bin:\$PATH\"" >> ~/.bash_profile
        echo "export POETRY_VIRTUALENVS_IN_PROJECT=true" >> ~/.bash_profile
        echo "export POETRY_VIRTUALENVS_PREFER_ACTIVE_PYTHON=true" >> ~/.bash_profile
        """
    )
    c.run(_sh)


@task(
    help={
        'domain': 'A domain name pointing to the server executeing this task',
        'password': 'Password of the proxy. Will generate a new one if not provided',
    }
)
def trojan(c: type[Connection], domain: str, password: str = ''):
    """
    Install a trojan proxy (requires docker and a domain name)
    """
    if not domain:
        print('domain needed')
        return
    path = '$HOME/trojan'
    if exists(c, path):
        print('trojan already installed')
        return
    if not password:
        password = os.urandom(16).hex()
        print(f'Generated password: {password}')
    c.run(f'mkdir -p {path} {path}/acme.sh', warn=True)
    c.run(
        'docker volume create --driver local --opt type=none '
        f'--opt device={path}/acme.sh --opt o=bind acme.sh'
    )
    # fetching SSL certs
    c.run(
        'docker run -i --rm --name acme.sh -p 80:80 -v acme.sh:/root/.acme.sh '
        '--entrypoint bash ghcr.io/ichuan/trojan-docker -c '
        '"/etc/init.d/nginx start ; /root/.acme.sh/acme.sh --home /root/.acme.sh '
        f'--issue --server letsencrypt -d {domain} -w /var/www/html"'
    )
    # start
    c.run(
        r"echo -e '#!/bin/bash\ndocker run --restart always -id --name trojan "
        r'-p 443:443 -p 80:80 -v acme.sh:/root/.acme.sh -e '
        rf'DOMAIN="{domain}" -e PASSWORD="{password}" '
        rf"ghcr.io/ichuan/trojan-docker' > {path}/start.sh"
    )
    c.run(f'chmod +x {path}/start.sh && {path}/start.sh')
    print(f'Done. Remember the password: {password}')

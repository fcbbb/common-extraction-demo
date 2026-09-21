PYTHON ?= python3

.PHONY: help setup check verify-results prepare-libcloud

help:
	@echo "make setup             创建 Conda 环境"
	@echo "make check             运行离线测试与语法检查"
	@echo "make verify-results    校验已提交的最终结果"
	@echo "make prepare-libcloud  下载并准备固定版本的 Libcloud 数据集"

setup:
	conda env create -f environment.yml

check:
	$(PYTHON) -m compileall -q demo tests
	$(PYTHON) -m pytest

verify-results:
	$(PYTHON) -m demo.eval.verify_release

prepare-libcloud:
	$(PYTHON) -m demo.prepare.prepare_dataset_libcloud_loadbalancer_real

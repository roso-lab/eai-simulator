.. EAI-HMRS Simulator documentation master file, created by
   sphinx-quickstart on Wed Jan  7 16:14:30 2026.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

EAI-HMRS Simulator
==================

EAI 是一个社会化物理仿真器（Social-Physical Simulator）。

它初始化一个包含人类与机器人的社会，通过物理引擎与社会规则引擎的耦合，模拟人机共融的复杂动态。控制算法接入的不是空场景，而是一个有规则、有角色、有信息流动、有多模态感知的社会。

从这里开始
----------

* **快速开始**：安装环境并运行第一个仿真场景。
* **环境配置**：使用 JSON 或 Env DIY 组合机器人、传感器和机械臂。
* **接口查询**：不启动 Isaac Sim，查询场景会创建的 ROS2 接口。
* **控制器开发**：了解底盘、强化学习和机械臂控制器的扩展方式。

核心原则
--------

* ROS2 接口使用机器人实例名作为一级 namespace。
* 机械臂使用 Isaac Sim 内部 ROS2 Bridge/OmniGraph，不使用 `tmp/` 文件。
* UR5 与 Z1 共享公共机械臂控制器，具体算法可以替换。
* 大型 USD 和模型资产通过 Hugging Face 按需下载，不放入 Git。

浏览器环境搭建工具：`打开 EAI Env DIY 工作台 <env_diy_tutorial.html>`_

.. toctree::
   :maxdepth: 2
   :titlesonly:
   :caption: 文档导航

   getting_started
   installation
   project_overview
   environments
   controller_guide
   interface_catalog
   payload

概述
====

EAI 是一个社会化物理仿真器（Social-Physical Simulator）。它通过物理引擎与社会规则引擎的耦合，为控制算法提供包含规则、角色、信息流动与多模态感知的人机共融社会。

快速开始
========

.. code-block:: bash

   python simulator.py --env robo

EAI Simulator 文档
==================

.. raw:: html

   <section class="eai-home-intro" aria-labelledby="eai-home-lede">
     <p class="eai-home-eyebrow">EAI SIMULATOR DOCUMENTATION</p>
     <p class="eai-home-lede" id="eai-home-lede">从第一个异构环境，到可复现的人机协作实验。</p>
     <p class="eai-home-summary">EAI Simulator 是面向人机共融研究的社会化物理仿真平台。组合环境与实体，连接感知和控制，并将实验流程沉淀为可复现的研究入口。</p>
     <nav class="eai-home-actions" aria-label="主要文档入口">
       <a class="eai-button eai-button--primary" href="project_overview.html">查看项目总览</a>
       <a class="eai-button eai-button--secondary" href="getting_started.html">开始第一次运行</a>
       <a class="eai-button eai-button--quiet" href="env_diy_tutorial.html">打开 Env DIY</a>
     </nav>
   </section>

.. image:: assets/media/demo.gif
   :alt: EAI Simulator 中的异构机器人、环境与任务运行演示
   :class: eai-home-demo
   :width: 100%

按任务进入
----------

不需要先通读全部文档。选择当前目标，沿对应路径完成一次可验证的工作流。

.. raw:: html

   <nav class="eai-workflow-grid" aria-label="按任务浏览文档">
     <a class="eai-workflow-card eai-workflow-card--start" href="getting_started.html">
       <span class="eai-workflow-stage">01 / START</span>
       <strong>第一次运行</strong>
       <span>完成工程安装与资产授权，启动 <code>robo</code> 环境。</span>
       <span class="eai-workflow-link">进入快速开始</span>
     </a>
     <a class="eai-workflow-card eai-workflow-card--build" href="environments.html">
       <span class="eai-workflow-stage">02 / BUILD</span>
       <strong>构建仿真环境</strong>
       <span>使用 JSON、Env DIY 或 3D 编辑器组合场景、机器人与 Payload。</span>
       <span class="eai-workflow-link">打开环境指南</span>
     </a>
     <a class="eai-workflow-card eai-workflow-card--connect" href="interface_catalog.html">
       <span class="eai-workflow-stage">03 / CONNECT</span>
       <strong>连接感知与控制</strong>
       <span>查询 ROS2、Nav2、Orsus、LiDAR 与机械臂接口。</span>
       <span class="eai-workflow-link">浏览接口目录</span>
     </a>
     <a class="eai-workflow-card eai-workflow-card--extend" href="controller_guide.html">
       <span class="eai-workflow-stage">04 / EXTEND</span>
       <strong>开发与扩展</strong>
       <span>接入传统控制器、RL 策略、外部算法与协作实验。</span>
       <span class="eai-workflow-link">阅读开发指南</span>
     </a>
   </nav>

最短成功路径
------------

.. container:: eai-command-dock

   .. raw:: html

      <header class="eai-command-dock__header">
        <span>
          <strong>启动 robo 环境</strong>
          <small>在已配置 Isaac Sim 的环境中运行</small>
        </span>
        <span class="eai-command-dock__language">BASH</span>
      </header>

   .. code-block:: bash

      conda activate env_isaaclab
      ./tools/install_packages.sh
      hf auth login
      python simulator.py --env robo

   .. raw:: html

      <footer class="eai-command-dock__footer">
        <span>需要 Ubuntu 22.04、Isaac Sim 5.1、Isaac Lab 2.x 与 gated assets 权限。</span>
        <a href="installation.html">查看完整安装指南</a>
      </footer>

继续探索
--------

.. raw:: html

   <nav class="eai-next-links" aria-label="继续探索">
     <a href="environments.html"><span>环境与实体</span><strong>构建可复用的仿真世界</strong></a>
     <a href="interface_catalog.html"><span>接口与控制</span><strong>连接 ROS2、传感器与机械臂</strong></a>
     <a href="controller_guide.html"><span>开发与实验</span><strong>扩展控制器与协作流程</strong></a>
     <a href="roadmap.html"><span>项目动态</span><strong>查看下一阶段功能规划</strong></a>
   </nav>

.. toctree::
   :hidden:
   :maxdepth: 1
   :titlesonly:
   :caption: 了解 EAI

   project_overview

.. toctree::
   :hidden:
   :maxdepth: 1
   :titlesonly:
   :caption: 开始使用

   getting_started
   installation

.. toctree::
   :hidden:
   :maxdepth: 1
   :titlesonly:
   :caption: 构建环境

   environments
   payload

.. toctree::
   :hidden:
   :maxdepth: 1
   :titlesonly:
   :caption: 连接与控制

   interface_catalog
   orsus_sensor
   pegasus_drones
   ur5_control

.. toctree::
   :hidden:
   :maxdepth: 1
   :titlesonly:
   :caption: 开发与扩展

   controller_guide

.. toctree::
   :hidden:
   :maxdepth: 1
   :titlesonly:
   :caption: 项目动态

   roadmap

.. toctree::
   :hidden:
   :maxdepth: 1
   :titlesonly:
   :caption: 社区

   community

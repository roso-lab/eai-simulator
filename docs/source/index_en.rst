:orphan:

EAI Simulator Documentation
===========================

.. raw:: html

   <section class="eai-home-intro" aria-labelledby="eai-home-lede">
     <p class="eai-home-eyebrow">EAI SIMULATOR DOCUMENTATION</p>
     <p class="eai-home-lede" id="eai-home-lede">From the first heterogeneous environment to reproducible human-robot collaboration experiments.</p>
     <p class="eai-home-summary">EAI Simulator is a social physical simulation platform for human-machine coexistence research. Compose environments and entities, connect perception and control, and turn experiment workflows into reproducible research entry points.</p>
     <nav class="eai-home-actions" aria-label="Primary documentation links">
       <a class="eai-button eai-button--primary" href="project_overview_en.html">View project overview</a>
       <a class="eai-button eai-button--secondary" href="getting_started_en.html">Run your first simulation</a>
       <a class="eai-button eai-button--quiet" href="environments_en.html#env-diy">Open Env DIY guide</a>
     </nav>
   </section>

.. image:: assets/media/demo.gif
   :alt: Heterogeneous robots, environments, and tasks running in EAI Simulator
   :class: eai-home-demo
   :width: 100%

Browse by Task
--------------

You do not need to read every page first. Choose your current goal and follow a focused path to a verifiable result.

.. raw:: html

   <nav class="eai-workflow-grid" aria-label="Browse documentation by task">
     <a class="eai-workflow-card eai-workflow-card--start" href="getting_started_en.html">
       <span class="eai-workflow-stage">01 / START</span>
       <strong>First simulation</strong>
       <span>Install the project, request asset access, and launch the <code>robo</code> environment.</span>
       <span class="eai-workflow-link">Open Quick Start</span>
     </a>
     <a class="eai-workflow-card eai-workflow-card--build" href="environments_en.html">
       <span class="eai-workflow-stage">02 / BUILD</span>
       <strong>Build an environment</strong>
       <span>Compose scenes, robots, and payloads with JSON, Env DIY, or the 3D editor.</span>
       <span class="eai-workflow-link">Open Environment Guide</span>
     </a>
     <a class="eai-workflow-card eai-workflow-card--connect" href="interface_catalog_en.html">
       <span class="eai-workflow-stage">03 / CONNECT</span>
       <strong>Connect perception and control</strong>
       <span>Find ROS2, Nav2, Orsus, LiDAR, and manipulator interfaces.</span>
       <span class="eai-workflow-link">Browse Interface Catalog</span>
     </a>
     <a class="eai-workflow-card eai-workflow-card--extend" href="controller_guide_en.html">
       <span class="eai-workflow-stage">04 / EXTEND</span>
       <strong>Develop and extend</strong>
       <span>Integrate conventional controllers, RL policies, external algorithms, and collaboration experiments.</span>
       <span class="eai-workflow-link">Read Controller Guide</span>
     </a>
   </nav>

Shortest Path to a Successful Run
---------------------------------

.. container:: eai-command-dock

   .. raw:: html

      <header class="eai-command-dock__header">
        <span>
          <strong>Launch the robo environment</strong>
          <small>Run inside an environment configured for Isaac Sim</small>
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
        <span>Requires Ubuntu 22.04, Isaac Sim 5.1, Isaac Lab 2.x, and access to the gated assets.</span>
        <a href="installation_en.html">View the complete installation guide</a>
      </footer>

Continue Exploring
------------------

.. raw:: html

   <nav class="eai-next-links" aria-label="Continue exploring">
     <a href="environments_en.html"><span>Environments and entities</span><strong>Build reusable simulation worlds</strong></a>
     <a href="interface_catalog_en.html"><span>Interfaces and control</span><strong>Connect ROS2, sensors, and manipulators</strong></a>
     <a href="controller_guide_en.html"><span>Development and experiments</span><strong>Extend controllers and collaboration workflows</strong></a>
     <a href="roadmap_en.html"><span>Project updates</span><strong>View the next-stage feature roadmap</strong></a>
   </nav>

.. toctree::
   :hidden:
   :maxdepth: 1
   :titlesonly:
   :caption: About EAI

   project_overview_en

.. toctree::
   :hidden:
   :maxdepth: 1
   :titlesonly:
   :caption: Get Started

   getting_started_en
   installation_en

.. toctree::
   :hidden:
   :maxdepth: 1
   :titlesonly:
   :caption: Build Environments

   environments_en
   payload_en

.. toctree::
   :hidden:
   :maxdepth: 1
   :titlesonly:
   :caption: Connect and Control

   interface_catalog_en
   orsus_sensor_en
   ur5_control_en

.. toctree::
   :hidden:
   :maxdepth: 1
   :titlesonly:
   :caption: Develop and Extend

   controller_guide_en
   human_assets_en
   pegasus_drones_en

.. toctree::
   :hidden:
   :maxdepth: 1
   :titlesonly:
   :caption: Project Updates

   roadmap_en

.. toctree::
   :hidden:
   :maxdepth: 1
   :titlesonly:
   :caption: Community

   community_en

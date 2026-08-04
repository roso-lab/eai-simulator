:orphan:

EAI Community
=============

.. raw:: html

   <div class="eai-community" data-eai-community data-count-label="topics">
     <header class="eai-community-intro">
       <div>
         <p class="eai-community-eyebrow">COMMUNITY FORUM</p>
         <p class="eai-community-lede">Share practical experience, troubleshoot problems, and help shape what EAI Simulator builds next.</p>
       </div>
       <a class="eai-button eai-button--primary" href="#cusdis_thread" data-eai-forum-cta>Join the discussion</a>
     </header>

     <section class="eai-community-toolbar" aria-label="Filter community topics">
       <label class="eai-community-search">
         <span aria-hidden="true"></span>
         <span class="visually-hidden">Search topics</span>
         <input type="search" placeholder="Search topics" autocomplete="off" data-eai-community-search>
       </label>
       <div class="eai-community-filters" role="group" aria-label="Topic categories">
         <button type="button" class="is-active" data-eai-community-filter="all" aria-pressed="true">All</button>
         <button type="button" data-eai-community-filter="announcement" aria-pressed="false">Announcements</button>
         <button type="button" data-eai-community-filter="help" aria-pressed="false">Help</button>
         <button type="button" data-eai-community-filter="build" aria-pressed="false">Environment</button>
         <button type="button" data-eai-community-filter="development" aria-pressed="false">Development</button>
       </div>
     </section>

     <div class="eai-community-layout">
       <main class="eai-community-topics">
         <div class="eai-community-list-heading">
           <strong>Featured topics</strong>
           <span data-eai-community-count>6 topics</span>
         </div>

         <div class="eai-community-topic-list" data-eai-community-list>
           <a class="eai-community-topic" href="community_guidelines_en.html" data-eai-community-topic data-category="announcement">
             <span class="eai-community-topic__marker" aria-hidden="true">NEWS</span>
             <span class="eai-community-topic__body">
               <span class="eai-community-topic__meta"><span class="eai-community-badge">PINNED</span> Community announcement</span>
               <strong>Read the community guidelines before joining a discussion</strong>
               <span>Learn how to ask focused questions, define scope, and report security issues through the correct channel.</span>
             </span>
             <span class="eai-community-topic__arrow" aria-hidden="true"></span>
           </a>

           <a class="eai-community-topic" href="getting_started_en.html" data-eai-community-topic data-category="help">
             <span class="eai-community-topic__marker" aria-hidden="true">START</span>
             <span class="eai-community-topic__body">
               <span class="eai-community-topic__meta">Help</span>
               <strong>What do I need for my first EAI Simulator run?</strong>
               <span>Follow the shortest path from environment setup and asset access to launching the <code>robo</code> scene.</span>
             </span>
             <span class="eai-community-topic__arrow" aria-hidden="true"></span>
           </a>

           <a class="eai-community-topic" href="installation_en.html" data-eai-community-topic data-category="help">
             <span class="eai-community-topic__marker" aria-hidden="true">SETUP</span>
             <span class="eai-community-topic__body">
               <span class="eai-community-topic__meta">Help</span>
               <strong>How do I diagnose installation or asset-loading failures?</strong>
               <span>Check the operating system, Isaac Sim environment, package installation, and gated asset access.</span>
             </span>
             <span class="eai-community-topic__arrow" aria-hidden="true"></span>
           </a>

           <a class="eai-community-topic" href="environments_en.html" data-eai-community-topic data-category="build">
             <span class="eai-community-topic__marker" aria-hidden="true">ENV</span>
             <span class="eai-community-topic__body">
               <span class="eai-community-topic__meta">Environment</span>
               <strong>How do I compose scenes, robots, and payloads?</strong>
               <span>Compare JSON, Env DIY, and the 3D editor to choose the right environment workflow for an experiment.</span>
             </span>
             <span class="eai-community-topic__arrow" aria-hidden="true"></span>
           </a>

           <a class="eai-community-topic" href="controller_guide_en.html" data-eai-community-topic data-category="development">
             <span class="eai-community-topic__marker" aria-hidden="true">CTRL</span>
             <span class="eai-community-topic__body">
               <span class="eai-community-topic__meta">Development</span>
               <strong>When should I use conventional control, RL, IK, or an external controller?</strong>
               <span>Use the task objective and data flow to decide where the controller belongs and how it should connect.</span>
             </span>
             <span class="eai-community-topic__arrow" aria-hidden="true"></span>
           </a>

           <a class="eai-community-topic" href="roadmap_en.html" data-eai-community-topic data-category="announcement">
             <span class="eai-community-topic__marker" aria-hidden="true">PLAN</span>
             <span class="eai-community-topic__body">
               <span class="eai-community-topic__meta">Project announcement</span>
               <strong>Next-stage roadmap and community proposals</strong>
               <span>Review capabilities still under development and submit proposals grounded in specific research scenarios.</span>
             </span>
             <span class="eai-community-topic__arrow" aria-hidden="true"></span>
           </a>
         </div>

         <p class="eai-community-empty" data-eai-community-empty hidden>No matching topics found.</p>
       </main>

       <aside class="eai-community-aside" aria-label="Community information">
         <section>
           <p class="eai-community-aside__label">Discussion channel</p>
           <h2>Post in the comments below</h2>
           <p>Join the discussion in the comment section at the bottom of this page. Comments are shown after moderator approval. For reproducible bugs and feature requests, use the channels listed in the SUPPORT document.</p>
           <a href="https://github.com/roso-lab/eai-simulator" target="_blank" rel="noopener noreferrer">GitHub repository</a>
         </section>

         <section id="community-guidelines">
           <p class="eai-community-aside__label">Community guidelines</p>
           <h2>Clear, considerate, reproducible</h2>
           <ul>
             <li>Search existing topics before opening a new one.</li>
             <li>Include versions, logs, and minimal reproduction steps.</li>
             <li>Do not disclose security vulnerabilities in public discussions.</li>
           </ul>
           <a href="community_guidelines_en.html">Read the complete community guidelines</a>
         </section>
       </aside>
     </div>
   </div>

EAI 社区
========

.. raw:: html

   <div class="eai-community" data-eai-community data-count-label="个主题">
     <header class="eai-community-intro">
       <div>
         <p class="eai-community-eyebrow">COMMUNITY FORUM</p>
         <p class="eai-community-lede">交流使用经验、排查问题，并参与 EAI Simulator 的后续建设。</p>
       </div>
       <a class="eai-button eai-button--primary" href="#cusdis_thread" data-eai-forum-cta>参与讨论</a>
     </header>

     <section class="eai-community-toolbar" aria-label="筛选社区主题">
       <label class="eai-community-search">
         <span aria-hidden="true"></span>
         <span class="visually-hidden">搜索主题</span>
         <input type="search" placeholder="搜索主题" autocomplete="off" data-eai-community-search>
       </label>
       <div class="eai-community-filters" role="group" aria-label="主题分类">
         <button type="button" class="is-active" data-eai-community-filter="all" aria-pressed="true">全部</button>
         <button type="button" data-eai-community-filter="announcement" aria-pressed="false">公告</button>
         <button type="button" data-eai-community-filter="help" aria-pressed="false">使用帮助</button>
         <button type="button" data-eai-community-filter="build" aria-pressed="false">环境搭建</button>
         <button type="button" data-eai-community-filter="development" aria-pressed="false">开发扩展</button>
       </div>
     </section>

     <div class="eai-community-layout">
       <main class="eai-community-topics">
         <div class="eai-community-list-heading">
           <strong>精选主题</strong>
           <span data-eai-community-count>6 个主题</span>
         </div>

         <div class="eai-community-topic-list" data-eai-community-list>
           <a class="eai-community-topic" href="community_guidelines.html" data-eai-community-topic data-category="announcement">
             <span class="eai-community-topic__marker" aria-hidden="true">公告</span>
             <span class="eai-community-topic__body">
               <span class="eai-community-topic__meta"><span class="eai-community-badge">置顶</span> 社区公告</span>
               <strong>参与讨论前，请先阅读社区规范</strong>
               <span>了解提问方式、问题边界和安全报告渠道，让每个话题都更容易得到有效回应。</span>
             </span>
             <span class="eai-community-topic__arrow" aria-hidden="true"></span>
           </a>

           <a class="eai-community-topic" href="getting_started.html" data-eai-community-topic data-category="help">
             <span class="eai-community-topic__marker" aria-hidden="true">入门</span>
             <span class="eai-community-topic__body">
               <span class="eai-community-topic__meta">使用帮助</span>
               <strong>第一次运行 EAI Simulator 需要完成哪些步骤？</strong>
               <span>从环境准备、资产授权到启动 <code>robo</code> 场景，按最短路径完成首次验证。</span>
             </span>
             <span class="eai-community-topic__arrow" aria-hidden="true"></span>
           </a>

           <a class="eai-community-topic" href="installation.html" data-eai-community-topic data-category="help">
             <span class="eai-community-topic__marker" aria-hidden="true">安装</span>
             <span class="eai-community-topic__body">
               <span class="eai-community-topic__meta">使用帮助</span>
               <strong>安装失败或资产无法加载时如何定位问题？</strong>
               <span>核对系统版本、Isaac Sim 环境、依赖安装和 gated assets 权限。</span>
             </span>
             <span class="eai-community-topic__arrow" aria-hidden="true"></span>
           </a>

           <a class="eai-community-topic" href="environments.html" data-eai-community-topic data-category="build">
             <span class="eai-community-topic__marker" aria-hidden="true">环境</span>
             <span class="eai-community-topic__body">
               <span class="eai-community-topic__meta">环境搭建</span>
               <strong>如何组合场景、机器人与 Payload？</strong>
               <span>比较 JSON、Env DIY 和 3D 编辑器三种路径，并选择适合实验的构建方式。</span>
             </span>
             <span class="eai-community-topic__arrow" aria-hidden="true"></span>
           </a>

           <a class="eai-community-topic" href="controller_guide.html" data-eai-community-topic data-category="development">
             <span class="eai-community-topic__marker" aria-hidden="true">控制</span>
             <span class="eai-community-topic__body">
               <span class="eai-community-topic__meta">开发扩展</span>
               <strong>传统控制、RL、IK 和外部控制器应该如何选择？</strong>
               <span>根据任务目标与数据流，确定控制器实现位置和接入接口。</span>
             </span>
             <span class="eai-community-topic__arrow" aria-hidden="true"></span>
           </a>

           <a class="eai-community-topic" href="roadmap.html" data-eai-community-topic data-category="announcement">
             <span class="eai-community-topic__marker" aria-hidden="true">规划</span>
             <span class="eai-community-topic__body">
               <span class="eai-community-topic__meta">项目公告</span>
               <strong>下一阶段功能规划与社区建议</strong>
               <span>查看仍在开发的能力，并围绕具体研究场景提交建议。</span>
             </span>
             <span class="eai-community-topic__arrow" aria-hidden="true"></span>
           </a>
         </div>

         <p class="eai-community-empty" data-eai-community-empty hidden>没有找到匹配的主题。</p>
       </main>

       <aside class="eai-community-aside" aria-label="社区信息">
         <section>
           <p class="eai-community-aside__label">讨论渠道</p>
           <h2>在下方评论区发帖</h2>
           <p>直接在页面底部的评论区参与讨论,内容经管理员审核后公开展示。可复现的 bug 与功能请求请通过 SUPPORT 文档中的渠道提交。</p>
           <a href="https://github.com/roso-lab/eai-simulator" target="_blank" rel="noopener noreferrer">GitHub 仓库</a>
         </section>

         <section id="community-guidelines">
           <p class="eai-community-aside__label">社区规范</p>
           <h2>清晰、友善、可复现</h2>
           <ul>
             <li>先搜索已有主题，再提交新问题。</li>
             <li>提供版本、日志和最小复现步骤。</li>
             <li>安全漏洞不要发布在公开讨论中。</li>
           </ul>
           <a href="community_guidelines.html">查看完整社区规范</a>
         </section>
       </aside>
     </div>
   </div>

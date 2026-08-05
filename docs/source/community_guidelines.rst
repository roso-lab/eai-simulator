:orphan:

社区规范
========

.. raw:: html

   <header class="eai-guidelines-intro">
     <div>
       <p class="eai-community-eyebrow">COMMUNITY GUIDELINES</p>
       <p class="eai-community-lede">让讨论保持清晰、友善并且可复现，使问题更容易得到有效回应。</p>
     </div>
     <a class="eai-button eai-button--primary" href="community.html#eai-forum-title">返回社区讨论</a>
   </header>
   <nav class="eai-guidelines-nav" aria-label="社区规范章节">
     <a href="#community-guideline-principles">基本原则</a>
     <a href="#community-guideline-before-posting">发帖前检查</a>
     <a href="#community-guideline-topics">选择话题</a>
     <a href="#community-guideline-template">问题模板</a>
     <a href="#community-guideline-moderation">审核与安全</a>
   </nav>

.. _community-guideline-principles:

基本原则
--------

- **清晰**：标题和正文应准确说明讨论对象，避免只有“无法运行”“请帮忙”等缺少上下文的描述。
- **友善**：针对技术事实和方案展开讨论，不攻击、嘲讽或贬低其他参与者。
- **可复现**：报告问题时提供足够的环境、配置和操作信息，让其他人可以验证现象。
- **聚焦**：一个帖子尽量只讨论一个核心问题；不同问题请分别发帖。

.. _community-guideline-before-posting:

发帖前检查
----------

1. 先搜索主题中心、现有讨论和项目文档，确认问题尚未被回答。
2. 确认使用的是受支持的 EAI Simulator 与 Isaac Sim 版本。
3. 将场景或配置缩减为能够触发问题的最小示例。
4. 删除日志、截图和配置中的账号、令牌、内部地址及其他敏感信息。
5. 可复现的产品缺陷与正式功能请求，应同时参考项目 ``SUPPORT`` 文档中的提交渠道。

.. _community-guideline-topics:

选择合适的话题
--------------

- **综合**：使用经验、工作流讨论，以及不属于其他分类的内容。
- **想法**：产品建议、研究方向和可以进一步讨论的改进方案。
- **投票**：需要社区共同选择优先级或设计方向的问题。
- **问答**：有明确问题、预期结果，并希望获得可验证答案的帖子。
- **展示与交流**：环境配置、实验结果、扩展组件和实践案例。

.. _community-guideline-template:

问题描述模板
------------

提交技术问题时，建议按以下结构组织正文：

.. code-block:: text

   EAI Simulator 版本：
   Isaac Sim 版本：
   操作系统与 GPU：
   场景、机器人与 Payload：

   期望结果：
   实际结果：

   最小复现步骤：
   1.
   2.
   3.

   相关日志或截图：
   已尝试的解决方法：

请用代码块粘贴日志和配置，长日志只保留与问题相关的前后文。截图应能看清关键状态，同时不要用截图代替可搜索的错误文本。

.. _community-guideline-moderation:

审核与安全
----------

评论提交后需要管理员审核才会公开。重复内容、广告、无关信息、恶意内容或包含敏感数据的帖子可能不会通过审核，已公开内容也可能被整理或移除。

安全漏洞、凭据泄露或可能影响用户资产的问题不要发布在公开讨论区。请按照项目仓库中的 `SECURITY.md <https://github.com/roso-lab/eai-simulator/blob/main/SECURITY.md>`_ 使用非公开渠道报告。

参与讨论即表示你同意遵守项目的 `行为准则 <https://github.com/roso-lab/eai-simulator/blob/main/CODE_OF_CONDUCT.md>`_。

.. raw:: html

   <div class="eai-guidelines-actions">
     <a class="eai-button eai-button--primary" href="community.html#eai-forum-title">进入社区讨论</a>
     <a class="eai-button eai-button--secondary" href="https://github.com/roso-lab/eai-simulator">打开 GitHub 仓库</a>
   </div>

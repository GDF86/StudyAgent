<template>
  <div class="shell">
    <aside class="sidebar">
      <RouterLink class="brand" to="/"><span class="brand-icon">S</span>StudyAgent</RouterLink>
      <p class="sidebar-label">学习空间</p>
      <nav aria-label="主导航">
        <RouterLink to="/" exact-active-class="active">首页 <span>↗</span></RouterLink>
        <RouterLink to="/knowledge-bases" active-class="active">知识库 <span>▤</span></RouterLink>
        <RouterLink to="/history" active-class="active">历史会话</RouterLink>
      </nav>
      <div class="sidebar-note"><span class="status-dot"></span> 从整理知识开始<p>一个主题，一个知识库。<br />为每一次深入学习做好准备。</p></div>
    </aside>
    <main><RouterView /></main>
  </div>
</template>

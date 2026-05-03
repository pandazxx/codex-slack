<template>
  <div id="root">
    <nav>
      <RouterLink to="/">Codex Slack</RouterLink>
    </nav>
    <main>
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
nav {
  background: #1e293b;
  color: #f8fafc;
  padding: 0.75rem 1.5rem;
  font-size: 1.1rem;
  font-weight: 600;
}
nav a { color: #93c5fd; }
main { padding: 1.5rem; max-width: 900px; margin: 0 auto; }
</style>

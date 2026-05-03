<template>
  <div id="root">
    <nav>
      <RouterLink to="/">Codex Slack</RouterLink>
      <RouterLink to="/settings" class="nav-settings">Settings</RouterLink>
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
  display: flex;
  align-items: center;
  gap: 1.5rem;
}
nav a { color: #93c5fd; }
.nav-settings { margin-left: auto; font-size: 0.9rem; font-weight: 400; }
main { padding: 1.5rem; max-width: 900px; margin: 0 auto; }
</style>

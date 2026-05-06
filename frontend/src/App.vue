<script setup>
import { ref, onMounted } from 'vue'
import RecentTopicsSidebar from './components/RecentTopicsSidebar.vue'

const masterVersion = ref('')

onMounted(async () => {
  try {
    const r = await fetch('/health')
    if (r.ok) masterVersion.value = (await r.json()).version || ''
  } catch { /* ignore */ }
})
</script>

<template>
  <div id="root">
    <nav>
      <RouterLink to="/">Codex Slack<span v-if="masterVersion" class="version">{{ masterVersion }}</span></RouterLink>
      <RouterLink to="/settings" class="nav-settings">Settings</RouterLink>
    </nav>
    <div class="body-layout">
      <RecentTopicsSidebar />
      <main>
        <RouterView />
      </main>
    </div>
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
.version { font-size: 0.7em; font-weight: 400; opacity: 0.65; margin-left: 0.4rem; }
.body-layout {
  display: flex;
  min-height: calc(100vh - 3rem);
}
main {
  flex: 1;
  padding: 1.5rem;
  min-width: 0;
}
</style>

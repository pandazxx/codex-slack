<script setup>
import { ref, onMounted, watch, provide } from 'vue'
import { useRoute } from 'vue-router'
import RecentTopicsSidebar from './components/RecentTopicsSidebar.vue'

const masterVersion = ref('')
const sidebarOpen = ref(false)
const route = useRoute()

provide('sidebar', { open: sidebarOpen, close: () => { sidebarOpen.value = false } })

watch(() => route.fullPath, () => { sidebarOpen.value = false })

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
      <button
        class="nav-toggle"
        :aria-expanded="sidebarOpen"
        aria-label="Toggle recent topics"
        @click="sidebarOpen = !sidebarOpen"
      >
        <span class="bar" /><span class="bar" /><span class="bar" />
      </button>
      <RouterLink to="/" class="brand">
        <span class="brand-text">Codex Slack</span>
        <span v-if="masterVersion" class="version">{{ masterVersion }}</span>
      </RouterLink>
      <RouterLink to="/settings" class="nav-settings">Settings</RouterLink>
    </nav>
    <div class="body-layout" :class="{ 'sidebar-open': sidebarOpen }">
      <div
        v-if="sidebarOpen"
        class="sidebar-backdrop"
        @click="sidebarOpen = false"
      />
      <RecentTopicsSidebar :open="sidebarOpen" @close="sidebarOpen = false" />
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
  gap: 1rem;
}
nav a { color: #93c5fd; }
.brand { display: inline-flex; align-items: baseline; gap: 0.4rem; }
.nav-settings { margin-left: auto; font-size: 0.9rem; font-weight: 400; }
.version { font-size: 0.7em; font-weight: 400; opacity: 0.65; }

.nav-toggle {
  display: none;
  background: none;
  border: none;
  padding: 0.4rem;
  margin: -0.4rem 0 -0.4rem -0.4rem;
  cursor: pointer;
  flex-direction: column;
  gap: 4px;
  width: 2rem;
  height: 2rem;
  align-items: center;
  justify-content: center;
}
.nav-toggle .bar {
  display: block;
  width: 1.25rem;
  height: 2px;
  background: #f8fafc;
  border-radius: 1px;
}

.body-layout {
  display: flex;
  min-height: calc(100vh - 3rem);
  position: relative;
}
.sidebar-backdrop {
  display: none;
}
main {
  flex: 1;
  padding: 1.5rem;
  min-width: 0;
}

@media (max-width: 768px) {
  nav { padding: 0.6rem 0.9rem; gap: 0.6rem; font-size: 1rem; }
  .nav-toggle { display: flex; }
  .brand-text { font-size: 0.95rem; }
  main { padding: 1rem 0.9rem; }
  .sidebar-backdrop {
    display: block;
    position: fixed;
    inset: 0;
    background: rgba(15, 23, 42, 0.45);
    z-index: 40;
  }
}
</style>

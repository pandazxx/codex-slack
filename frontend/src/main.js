import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import WorkspaceList from './views/WorkspaceList.vue'
import WorkspaceDetail from './views/WorkspaceDetail.vue'
import TopicChat from './views/TopicChat.vue'
import ArchivedWorkspaces from './views/ArchivedWorkspaces.vue'
import ArchivedTopics from './views/ArchivedTopics.vue'
import Settings from './views/Settings.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: WorkspaceList },
    { path: '/archived', component: ArchivedWorkspaces },
    { path: '/settings', component: Settings },
    { path: '/workspaces/:id', component: WorkspaceDetail },
    { path: '/workspaces/:id/archived-topics', component: ArchivedTopics },
    { path: '/workspaces/:wsId/topics/:topicId', component: TopicChat },
  ],
})

createApp(App).use(router).mount('#app')

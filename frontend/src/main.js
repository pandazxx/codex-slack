import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import WorkspaceList from './views/WorkspaceList.vue'
import WorkspaceDetail from './views/WorkspaceDetail.vue'
import TopicChat from './views/TopicChat.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: WorkspaceList },
    { path: '/workspaces/:id', component: WorkspaceDetail },
    { path: '/workspaces/:wsId/topics/:topicId', component: TopicChat },
  ],
})

createApp(App).use(router).mount('#app')

import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '../views/HomeView.vue'
import KnowledgeBasesView from '../views/KnowledgeBasesView.vue'
import DocumentsView from '../views/DocumentsView.vue'
import HistoryView from '../views/HistoryView.vue'
import ConversationView from '../views/ConversationView.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: HomeView },
    { path: '/knowledge-bases', component: KnowledgeBasesView },
    { path: '/knowledge-bases/:id', component: DocumentsView },
    { path: '/history', component: HistoryView },
    { path: '/conversations/:id', component: ConversationView },
    { path: '/knowledge-bases/:id/chat', redirect: to => `/knowledge-bases/${to.params.id}` },
    { path: '/knowledge-bases/:id/agent', redirect: to => `/knowledge-bases/${to.params.id}` },
    { path: '/comparison', redirect: '/knowledge-bases' },
    { path: '/preferences', redirect: '/knowledge-bases' },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

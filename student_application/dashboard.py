from jet.dashboard import modules
from jet.dashboard.dashboard import Dashboard


class CustomIndexDashboard(Dashboard):
    columns = 3

    def init_with_context(self, context):
        self.children.append(modules.LinkList(
            'Quick Links',
            children=[
                {'title': 'Student Home', 'url': '/'},
                {'title': 'Staff Dashboard', 'url': '/staff/'},
                {'title': 'Director Dashboard', 'url': '/director/'},
                {'title': 'Available Offices', 'url': '/offices/'},
            ],
            column=0,
            order=0,
        ))


        self.children.append(modules.RecentActions(
            'Recent Actions',
            limit=10,
            column=1,
            order=0,
        ))


        self.children.append(modules.AppList(
            'Application Models',
            column=2,
            order=0,
        ))

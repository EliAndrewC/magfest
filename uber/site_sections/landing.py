from uber.decorators import all_renderable


@all_renderable(public=True)
class Root:
    def index(self):
        return {}

    def invalid(self, **params):
        return {'message': params.get('message')}

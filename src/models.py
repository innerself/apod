from peewee import BooleanField, CharField, DateField, IntegerField, Model, SqliteDatabase, TextField

db = SqliteDatabase('db.sqlite')


def init_db():
    db.connect()
    db.create_tables([Issue])


class Issue(Model):
    id = IntegerField(primary_key=True)
    title = CharField(max_length=255)
    body = TextField()
    issue_url = CharField(max_length=255, unique=True)
    image_url = CharField(max_length=255)
    pub_date = DateField()
    published = BooleanField(default=False)

    class Meta:
        database = db

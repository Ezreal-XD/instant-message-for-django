from django.db import models


class ChatInfo(models.Model):
    '''
    类名即表名。需要注意的是，ORM类需要继承自`models.Model`
    通过调用models的方法创建列（字段）
    '''
    from_wh = models.CharField(max_length=64)
    to_who = models.CharField(max_length=64)
    content = models.CharField(max_length=1024)
    send_time = models.DateTimeField()


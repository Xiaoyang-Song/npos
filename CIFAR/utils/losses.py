
"""
V1: not considering self-supervised positive pairs
Aapted from SupCLR: https://github.com/HobbitLong/SupContrast/
"""
from __future__ import print_function

import torch
import torch.nn as nn
import torch.nn.functional as F
import time
from copy import deepcopy


class SupUniformLoss(nn.Module):
    '''
    Uniformity Loss or Dispersion Loss
    '''
    def __init__(self, args, model, train_loader, temperature= 0.1, base_temperature=0.1):
        super(SupUniformLoss, self).__init__()
        self.args = args
        self.temperature = temperature
        self.base_temperature = base_temperature
        # self.prototypes = torch.nn.Parameter(torch.randn(self.args.n_cls,self.args.feat_dim).cuda())
        self.register_buffer("prototypes", torch.zeros(self.args.n_cls,self.args.feat_dim))
        # self.prototypes = self.prototypes.cuda()
        self.model = model
        self.train_loader = train_loader

        self.init_class_prototypes()
        # self.shadow = self.prototypes.clone()

    def forward(self, features, labels=None, mask=None):
        """Compute loss for model. 
        Args:
            features: hidden vector of shape [bsz, ...].
            labels: ground truth of shape [bsz].
            mask: contrastive mask of shape [bsz, bsz], mask_{i,j}=1 if sample j
                has the same class as sample i. Can be asymmetric.
        Returns:
            A loss scalar.
        """
        device = torch.device('cuda')
        

        if len(features.shape)  != 2:
            raise ValueError('`features` needs to be [bsz, hidden_dim],'
                             '2 dimensions are required')
        # for j, feature in enumerate(features):
        prototypes = self.prototypes
        for j in range(len(features)):
            # self.prototypes[labels[j].item()] = self.shadow[labels[j].item()] *0.9 + feature*0.1
            # self.prototypes[labels[j].item()] = self.prototypes[labels[j].item()] *self.args.proto_m + feature.data*(1-self.args.proto_m)
            prototypes[labels[j].item()] = F.normalize(prototypes[labels[j].item()] *self.args.proto_m + features[j]*(1-self.args.proto_m), dim=0)
            # self.shadow = self.prototypes.clone()
        self.prototypes = prototypes.detach()
        # prototype uniform loss

        labels = torch.arange(0, self.args.n_cls).cuda()
        batch_size = prototypes.shape[0]

        if labels is not None:
            labels = labels.contiguous().view(-1, 1)
            if labels.shape[0] != batch_size:
                raise ValueError('Num of labels does not match num of features')
            mask = (1- torch.eq(labels, labels.T).float()).to(device)
        elif mask is not None: # if mask is provided
            mask = (1 - mask.float()).to(device)

        # V1: simple setting where anchor_feature = contrast_feature = features 
        anchor_count = 1
        contrast_feature = prototypes
        anchor_feature = prototypes

        # compute logits

        # V1: shifted dot product of normalized representations
        # anchor_dot_contrast = torch.matmul(anchor_feature, contrast_feature.T)
        # logits = self.temperature*(anchor_dot_contrast*2 - 2)

        #V1.1: no shift logit, temperature 0.1
        logits = torch.div(
            torch.matmul(anchor_feature, contrast_feature.T),
            self.temperature)

        # for numerical stability
        # logits_max, _ = torch.max(anchor_dot_contrast, dim=1, keepdim=True)
        # logits = anchor_dot_contrast - logits_max.detach()

        # tile mask
        # mask = mask.repeat(anchor_count, contrast_count)
        # mask-out self
        logits_mask = torch.scatter(
            torch.ones_like(mask),
            1,
            torch.arange(batch_size * anchor_count).view(-1, 1).to(device),
            0
        )
        mask = mask * logits_mask

        # compute log_prob
        # exp_logits = torch.exp(logits) * logits_mask

        # compute mean of log-likelihood over negatives
        mean_prob_neg = torch.log((mask * torch.exp(logits)).sum(1) / mask.sum(1))
        mean_prob_neg = mean_prob_neg[~torch.isnan(mean_prob_neg)]
        # loss
        # V1
        # loss = mean_prob_neg.mean()

        # V1.1
        loss = self.temperature / self.base_temperature * mean_prob_neg.mean()

        return loss
    def init_class_prototypes(self):
        """Initialize class prototypes"""

        # switch to evaluate mode
        self.model.eval()
        start = time.time()
        prototype_counts = [0]*self.args.n_cls
        with torch.no_grad():
            prototypes = torch.zeros(self.args.n_cls,self.args.feat_dim).cuda()
            for i, (input, target) in enumerate(self.train_loader):
                input = torch.cat([input[0], input[1]], dim=0).cuda()
                target = target.repeat(2).cuda()
                penultimate = self.model.encoder(input)
                # features= self.model.head(penultimate)
                features= F.normalize(self.model.head(penultimate), dim=1)
                for j, feature in enumerate(features):
                    prototypes[target[j].item()] += feature
                    prototype_counts[target[j].item()] += 1
            for cls in range(self.args.n_cls):
                prototypes[cls] /=  prototype_counts[cls] 
                # prototypes[target[j].item()] = prototypes[target[j].item()]*(count-1)/count + feature/count

            # measure elapsed time
            duration = time.time() - start
            print(f'Time to initialize prototypes: {duration:.3f}')
            prototypes = F.normalize(prototypes, dim=1)
            # self.prototypes = torch.nn.Parameter(prototypes)
            self.prototypes = prototypes



class vMFLoss(nn.Module):
    '''
    vMF loss
    temp: 0.1 -> kappa: 10
    temp: 0.067 -> kappa: 15
    temp: 0.05 -> kappa: 20
    '''
    def __init__(self, args, model, train_loader, temperature= 0.1):
        super(vMFLoss, self).__init__()
        self.args = args
        self.temperature = temperature
        self.register_buffer("prototypes", torch.zeros(self.args.n_cls,self.args.feat_dim))
        self.model = model
        self.train_loader = train_loader
        self.init_class_prototypes()

    def forward(self, features, labels=None, mask=None):
        device = torch.device('cuda')
        if len(features.shape)  != 2:
            raise ValueError('`features` needs to be [bsz, hidden_dim],'
                             '2 dimensions are required')
        # for j, feature in enumerate(features):
        prototypes = self.prototypes
        for j in range(len(features)):
            prototypes[labels[j].item()] = F.normalize(prototypes[labels[j].item()] *self.args.proto_m + features[j]*(1-self.args.proto_m), dim=0)
        self.prototypes = prototypes.detach()
        batch_size = prototypes.shape[0]
        proxy_labels = torch.arange(0, self.args.n_cls).to(device)
        batch_size = features.shape[0]
        labels = labels.contiguous().view(-1, 1)
        if labels.shape[0] != batch_size:
            raise ValueError('Num of labels does not match num of features')
        mask = torch.eq(labels, proxy_labels.T).float().to(device) #mask shape: 512, 10
        anchor_feature = features #2*bz, 128
        contrast_feature = prototypes #10, 128
        # compute logits
        anchor_dot_contrast = torch.div(
            torch.matmul(anchor_feature, contrast_feature.T),
            self.temperature)
        # for numerical stability
        logits_max, _ = torch.max(anchor_dot_contrast, dim=1, keepdim=True)
        logits = anchor_dot_contrast - logits_max.detach()
        # now every logit is negative
        # compute log_prob
        exp_logits = torch.exp(logits) 
        log_prob = logits - torch.log(exp_logits.sum(1, keepdim=True))

        # compute mean of log-likelihood over positive
        mean_log_prob_pos = (mask * log_prob).sum(1) 

        # loss
        loss = -mean_log_prob_pos.mean()

        return loss
    def init_class_prototypes(self):
        """Initialize class prototypes"""

        # switch to evaluate mode
        self.model.eval()
        start = time.time()
        prototype_counts = [0]*self.args.n_cls
        with torch.no_grad():
            prototypes = torch.zeros(self.args.n_cls,self.args.feat_dim).cuda()
            for i, (input, target) in enumerate(self.train_loader):
                input = torch.cat([input[0], input[1]], dim=0).cuda()
                target = target.repeat(2).cuda()
                penultimate = self.model.encoder(input)
                # features= self.model.head(penultimate)
                features= F.normalize(self.model.head(penultimate), dim=1)
                for j, feature in enumerate(features):
                    prototypes[target[j].item()] += feature
                    prototype_counts[target[j].item()] += 1
            for cls in range(self.args.n_cls):
                prototypes[cls] /=  prototype_counts[cls] 

            # measure elapsed time
            duration = time.time() - start
            print(f'Time to initialize prototypes: {duration:.3f}')
            prototypes = F.normalize(prototypes, dim=1)
            # self.prototypes = torch.nn.Parameter(prototypes)
            self.prototypes = prototypes

# class SupAlignmentLoss(nn.Module):
#     def __init__(self, temperature= 1, base_temperature=1):
#         super(SupAlignmentLoss, self).__init__()
#         self.temperature = temperature
#         self.base_temperature = base_temperature

#     def forward(self, features, labels=None, mask=None):
#         """Compute loss for model. 
#         Args:
#             features: hidden vector of shape [bsz, ...].
#             labels: ground truth of shape [bsz].
#             mask: contrastive mask of shape [bsz, bsz], mask_{i,j}=1 if sample j
#                 has the same class as sample i. Can be asymmetric.
#         Returns:
#             A loss scalar.
#         """
#         device = torch.device('cuda')

#         if len(features.shape)  != 2:
#             raise ValueError('`features` needs to be [bsz, hidden_dim],'
#                              '2 dimensions are required')

#         batch_size = features.shape[0]
#         if labels is not None and mask is not None:
#             raise ValueError('Cannot define both `labels` and `mask`')
#         # elif labels is None and mask is None:
#         #     mask = torch.eye(batch_size, dtype=torch.float32).to(device)
#         elif labels is not None:
#             labels = labels.contiguous().view(-1, 1)
#             if labels.shape[0] != batch_size:
#                 raise ValueError('Num of labels does not match num of features')
#             mask = torch.eq(labels, labels.T).float().to(device)
#         elif mask is not None: # if mask is provided
#             mask = mask.float().to(device)

#         # V1: simple setting where anchor_feature = contrast_feature = features 
#         anchor_count = 1
#         contrast_feature = features
#         anchor_feature = features

#         # compute logits

#         # V1: shifted dot product of normalized representations
#         # anchor_dot_contrast = torch.matmul(anchor_feature, contrast_feature.T)
#         # logits = self.temperature*(anchor_dot_contrast*2 - 2)

#         #V1.1: no shift logit, temperature 0.1
#         logits = torch.div(
#             torch.matmul(anchor_feature, contrast_feature.T),
#             self.temperature)

#         # for numerical stability
#         # logits_max, _ = torch.max(anchor_dot_contrast, dim=1, keepdim=True)
#         # logits = anchor_dot_contrast - logits_max.detach()

#         # tile mask
#         # mask = mask.repeat(anchor_count, contrast_count)
#         # mask-out self-contrast cases
#         logits_mask = torch.scatter(
#             torch.ones_like(mask),
#             1,
#             torch.arange(batch_size * anchor_count).view(-1, 1).to(device),
#             0
#         )
#         mask = mask * logits_mask

#         # compute log_prob
#         # exp_logits = torch.exp(logits) * logits_mask

#         # compute mean of log-likelihood over positives
#         mean_prob_pos = (mask * logits).sum(1) / mask.sum(1)
#         mean_prob_pos = mean_prob_pos[~torch.isnan(mean_prob_pos)]
#         # loss
#         # V1
#         # loss = mean_prob_pos.mean()

#         # V1.1
#         loss = self.temperature / self.base_temperature * mean_prob_pos.mean()
#         if torch.isnan(loss.data):
#             print("mean_Prob_pos: ",mean_prob_pos)
#             print("logits: ", logits)
#         return loss

def binarize(T, nb_classes):
    T = T.cpu().numpy()
    import sklearn.preprocessing
    T = sklearn.preprocessing.label_binarize(
        T, classes = range(0, nb_classes)
    )
    T = torch.FloatTensor(T).cuda()
    return T

def l2_norm(input):
    input_size = input.size()
    buffer = torch.pow(input, 2)
    normp = torch.sum(buffer, 1).add_(1e-12)
    norm = torch.sqrt(normp)
    _output = torch.div(input, norm.view(-1, 1).expand_as(input))
    output = _output.view(input_size)
    return output

class Proxy_Anchor(torch.nn.Module):
    def __init__(self, nb_classes, sz_embed, mrg = 0.1, alpha = 32):
        torch.nn.Module.__init__(self)
        # Proxy Anchor Initialization
        self.proxies = torch.nn.Parameter(torch.randn(nb_classes, sz_embed).cuda())
        nn.init.kaiming_normal_(self.proxies, mode='fan_out')

        self.nb_classes = nb_classes
        self.sz_embed = sz_embed
        self.mrg = mrg
        self.alpha = alpha
        
    def forward(self, X, T):
        P = self.proxies

        cos = F.linear(l2_norm(X), l2_norm(P))  # Calcluate cosine similarity
        P_one_hot = binarize(T = T, nb_classes = self.nb_classes)
        N_one_hot = 1 - P_one_hot
    
        pos_exp = torch.exp(-self.alpha * (cos - self.mrg))
        neg_exp = torch.exp(self.alpha * (cos + self.mrg))

        with_pos_proxies = torch.nonzero(P_one_hot.sum(dim = 0) != 0).squeeze(dim = 1)   # The set of positive proxies of data in the batch
        num_valid_proxies = len(with_pos_proxies)   # The number of positive proxies
        
        P_sim_sum = torch.where(P_one_hot == 1, pos_exp, torch.zeros_like(pos_exp)).sum(dim=0) 
        N_sim_sum = torch.where(N_one_hot == 1, neg_exp, torch.zeros_like(neg_exp)).sum(dim=0)
        
        pos_term = torch.log(1 + P_sim_sum).sum() / num_valid_proxies
        neg_term = torch.log(1 + N_sim_sum).sum() / self.nb_classes
        loss = pos_term + neg_term     
        
        return loss

class SupConLoss(nn.Module):
    """Supervised Contrastive Learning: https://arxiv.org/pdf/2004.11362.pdf.
    It also supports the unsupervised contrastive loss in SimCLR"""
    def __init__(self, temperature=0.07, contrast_mode='all',
                 base_temperature=0.07):
        super(SupConLoss, self).__init__()
        self.temperature = temperature
        self.contrast_mode = contrast_mode
        self.base_temperature = base_temperature

    def forward(self, features, labels=None, mask=None):
        """Compute loss for model. If both `labels` and `mask` are None,
        it degenerates to SimCLR unsupervised loss:
        https://arxiv.org/pdf/2002.05709.pdf
        Args:
            features: hidden vector of shape [bsz, n_views, ...].
            labels: ground truth of shape [bsz].
            mask: contrastive mask of shape [bsz, bsz], mask_{i,j}=1 if sample j
                has the same class as sample i. Can be asymmetric.
        Returns:
            A loss scalar.
        """
        device = (torch.device('cuda')
                  if features.is_cuda
                  else torch.device('cpu'))

        if len(features.shape) < 3:
            raise ValueError('`features` needs to be [bsz, n_views, ...],'
                             'at least 3 dimensions are required')
        if len(features.shape) > 3:
            features = features.view(features.shape[0], features.shape[1], -1)

        batch_size = features.shape[0]
        if labels is not None and mask is not None:
            raise ValueError('Cannot define both `labels` and `mask`')
        elif labels is None and mask is None:
            mask = torch.eye(batch_size, dtype=torch.float32).to(device)
        elif labels is not None:
            labels = labels.contiguous().view(-1, 1)
            if labels.shape[0] != batch_size:
                raise ValueError('Num of labels does not match num of features')
            mask = torch.eq(labels, labels.T).float().to(device)
        else:
            mask = mask.float().to(device)

        contrast_count = features.shape[1]
        contrast_feature = torch.cat(torch.unbind(features, dim=1), dim=0)
        if self.contrast_mode == 'one':
            anchor_feature = features[:, 0]
            anchor_count = 1
        elif self.contrast_mode == 'all':
            anchor_feature = contrast_feature
            anchor_count = contrast_count
        else:
            raise ValueError('Unknown mode: {}'.format(self.contrast_mode))

        # compute logits
        anchor_dot_contrast = torch.div(
            torch.matmul(anchor_feature, contrast_feature.T),
            self.temperature)
        # for numerical stability
        logits_max, _ = torch.max(anchor_dot_contrast, dim=1, keepdim=True)
        logits = anchor_dot_contrast - logits_max.detach()

        # tile mask
        mask = mask.repeat(anchor_count, contrast_count)
        # mask-out self-contrast cases
        logits_mask = torch.scatter(
            torch.ones_like(mask),
            1,
            torch.arange(batch_size * anchor_count).view(-1, 1).to(device),
            0
        )
        mask = mask * logits_mask

        # compute log_prob
        exp_logits = torch.exp(logits) * logits_mask
        log_prob = logits - torch.log(exp_logits.sum(1, keepdim=True))

        # compute mean of log-likelihood over positive
        mean_log_prob_pos = (mask * log_prob).sum(1) / mask.sum(1)

        # loss
        loss = - (self.temperature / self.base_temperature) * mean_log_prob_pos
        loss = loss.view(anchor_count, batch_size).mean()

        return loss

class SupConProxyLoss(nn.Module):
    def __init__(self, args, temperature=0.07, base_temperature=0.07):
        super(SupConProxyLoss, self).__init__()
        self.args = args
        self.temperature = temperature
        self.base_temperature = base_temperature

    def forward(self, features, prototypes, labels):
        device = (torch.device('cuda')
                  if features.is_cuda
                  else torch.device('cpu'))
        proxy_labels = torch.arange(0, self.args.n_cls).to(device)
        batch_size = features.shape[0]
        labels = labels.contiguous().view(-1, 1)
        if labels.shape[0] != batch_size:
            raise ValueError('Num of labels does not match num of features')
        mask = torch.eq(labels, proxy_labels.T).float().to(device)

        
        anchor_feature = features
        contrast_feature = prototypes
        # anchor_count = 1

        # compute logits
        anchor_dot_contrast = torch.div(
            torch.matmul(anchor_feature, contrast_feature.T),
            self.temperature)
        # for numerical stability
        logits_max, _ = torch.max(anchor_dot_contrast, dim=1, keepdim=True)
        logits = anchor_dot_contrast - logits_max.detach()

        # compute log_prob
        # exp_logits = torch.exp(logits) * logits_mask
        exp_logits = torch.exp(logits) 
        log_prob = logits - torch.log(exp_logits.sum(1, keepdim=True))

        # compute mean of log-likelihood over positive
        mean_log_prob_pos = (mask * log_prob).sum(1) 

        # loss
        loss = - (self.temperature / self.base_temperature) * mean_log_prob_pos.mean()
        # loss = loss.view(anchor_count, batch_size).mean()

        return loss
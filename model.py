import torch
from torch import nn
import torch.nn.functional as F


class MatchLSTM(nn.Module):
    def __init__(self, config, word2vec):
        super(MatchLSTM, self).__init__()
        self.config = config

        use_cuda = config.yes_cuda > 0 and torch.cuda.is_available()
        self.device = torch.device("cuda" if use_cuda else "cpu")

        self.word_embed = nn.Embedding(len(word2vec), len(word2vec[0]),
                                       padding_idx=0)
        self.word_embed.weight.data.copy_(torch.from_numpy(word2vec))
        self.word_embed.weight.requires_grad = False

        self.w_e = nn.Parameter(torch.Tensor(config.hidden_size))
        nn.init.uniform_(self.w_e)

        self.linear_s = nn.Linear(in_features=config.hidden_size,
                                  out_features=config.hidden_size, bias=False)
        self.linear_t = nn.Linear(in_features=config.hidden_size,
                                  out_features=config.hidden_size, bias=False)
        self.linear_m = nn.Linear(in_features=config.hidden_size,
                                  out_features=config.hidden_size, bias=False)
        self.fc = nn.Linear(in_features=config.hidden_size,
                            out_features=config.num_classes)
        self.init_linears()

        self.lstm_prem = nn.LSTMCell(config.embedding_dim, config.hidden_size)
        self.lstm_hypo = nn.LSTMCell(config.embedding_dim, config.hidden_size)
        self.lstm_match = nn.LSTMCell(2*config.hidden_size, config.hidden_size)

    def init_linears(self):
        nn.init.xavier_uniform_(self.linear_s.weight)
        nn.init.xavier_uniform_(self.linear_t.weight)
        nn.init.xavier_uniform_(self.linear_m.weight)
        nn.init.xavier_uniform_(self.fc.weight)
        nn.init.uniform_(self.fc.bias)

    def forward(self, premise_tpl, hypothesis_tpl):
        premise, premise_len = premise_tpl
        hypothesis, hypothesis_len = hypothesis_tpl

        # (batch_size, max_len) -> (batch_size, max_len, embed_dim)
        premise_embed = self.word_embed(premise.to(self.device))
        hypothesis_embed = self.word_embed(hypothesis.to(self.device))

        batch_size = premise_embed.size(0)

        outputs = torch.zeros((batch_size, self.config.num_classes),
                              device=self.device)

        for i, (prem_emb, prem_len, hypo_emb, hypo_len) in \
                enumerate(zip(premise_embed, premise_len,
                              hypothesis_embed, hypothesis_len)):

            # premise
            h_s = torch.zeros((prem_len.item(), self.config.hidden_size),
                              device=self.device)
            for j, prem_j in enumerate(prem_emb[:prem_len.item()]):
                h_s_j, _ = self.lstm_prem(torch.unsqueeze(prem_j, 0))
                h_s[j] = h_s_j

            # hypothesis
            h_t = torch.zeros((hypo_len.item(), self.config.hidden_size),
                              device=self.device)
            for k, hypo_k in enumerate(hypo_emb[:hypo_len.item()]):
                h_t_k, _ = self.lstm_hypo(torch.unsqueeze(hypo_k, 0))
                h_t[k] = h_t_k

            # h_m_{k-1}
            h_m_km1 = torch.zeros(self.config.hidden_size, device=self.device)
            h_m_k = None

            for k in range(hypo_len.item()):
                h_t_k = h_t[k]

                # Equation (6)
                e_kj_tensor = torch.zeros(prem_len.item(), device=self.device)
                for j in range(prem_len.item()):
                    e_kj = torch.dot(self.w_e,
                                     torch.tanh(self.linear_s(h_s[j]) +
                                                self.linear_t(h_t_k) +
                                                self.linear_m(h_m_km1)))
                    e_kj_tensor[j] = e_kj

                # Equation (3)
                alpha_kj = F.softmax(e_kj_tensor, dim=0)

                # Equation (2)
                a_k = torch.zeros(self.config.hidden_size, device=self.device)
                for j in range(prem_len.item()):
                    alpha_h = alpha_kj[j] * h_s[j]
                    for idx in range(self.config.hidden_size):
                        a_k[idx] += alpha_h[idx]  # element-wise sum

                # Equation (7)
                m_k = torch.cat((a_k, h_t_k), 0)

                # Equation (8)
                h_m_k, _ = self.lstm_match(torch.unsqueeze(m_k, 0))

                h_m_km1 = h_m_k[0]

            outputs[i] = self.fc(h_m_k[0])

        return F.log_softmax(outputs, dim=1)

    def get_req_grad_params(self, debug=False):
        print('#parameters: ', end='')
        params = list()
        total_size = 0

        def multiply_iter(p_list):
            out = 1
            for _p in p_list:
                out *= _p
            return out

        for p in self.parameters():
            if p.requires_grad:
                params.append(p)
                total_size += multiply_iter(p.size())
            if debug:
                print(p.requires_grad, p.size())
        print('{:,}'.format(total_size))
        return params
